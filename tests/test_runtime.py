import asyncio
import json

import httpx2 as httpx
import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from agent_demo.config import ROOT, Config
from agent_demo.documents import Documents
from agent_demo.runtime import create_agent, run_turn
from agent_demo.storage import Session, Store


def sse(delta, finish_reason):
    chunk = {
        "id": "test-completion", "object": "chat.completion.chunk", "created": 1,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"},
        content=f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode(),
    )


def tool_delta(name, arguments, call_id):
    return {"role": "assistant", "tool_calls": [{
        "index": 0, "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }]}


async def test_real_request_serialization_and_tool_loop(tmp_path):
    requests = []
    store = Store(tmp_path)
    user_id = str(store.identity().user_id)

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["user_id"] == user_id
        assert body["thinking"] == {"type": "disabled"}
        assert body["stream"] is True
        if len(requests) == 1:
            return sse(tool_delta("search_documents", {"query": "住宿"}, "s1"), "tool_calls")
        if len(requests) == 2:
            return sse(tool_delta("read_document", {"document_id": "travel"}, "r1"), "tool_calls")
        return sse({"role": "assistant", "content": "住宿限额为450元。[travel §T3]"}, "stop")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = create_agent(Config("test-key"), user_id, Documents(ROOT / "data/fixtures"),
                             http_client=client)
        session = Session(model="deepseek-flash")
        output = []
        result = await run_turn(agent, session, "住宿限额是多少？", store, output.append)
        restored = Store(tmp_path).load(str(session.id))
        # provider_details 的时间在 JSON 中为字符串，比较持久化内容而非内存类型。
        assert restored.model_dump(mode="json") == session.model_dump(mode="json")
        await run_turn(agent, restored, "请复述限额。", store, output.append)
    assert len(requests) == 4
    assert "450" in result.output
    assert "450" in "".join(output)
    tool_results = [message for message in requests[-1]["messages"] if message["role"] == "tool"]
    assert {message["tool_call_id"] for message in tool_results} == {"s1", "r1"}
    assert "450" in tool_results[-1]["content"]
    assert requests[-1]["messages"][-1]["content"] == "请复述限额。"


@pytest.mark.parametrize("failure", ["api", "cancel"])
async def test_failure_or_cancel_keeps_successful_history(tmp_path, failure):
    store = Store(tmp_path)
    session = Session(model="deepseek-flash", messages=[
        ModelRequest(parts=[UserPromptPart("你好")]),
        ModelResponse(parts=[TextPart("你好，已有历史。")]),
    ])
    store.save(session)
    path = tmp_path / "sessions" / f"{session.id}.json"
    before = path.read_bytes()
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return sse(tool_delta("search_documents", {"query": "差旅"}, "s1"), "tool_calls")
        if failure == "cancel":
            raise asyncio.CancelledError()
        return httpx.Response(401, json={"error": {"message": "test unauthorized", "type": "auth"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        agent = create_agent(Config("test-key"), "test-user", Documents(ROOT / "data/fixtures"),
                             http_client=client)
        expected = asyncio.CancelledError if failure == "cancel" else Exception
        with pytest.raises(expected):
            await run_turn(agent, session, "报销？", store, lambda text: None)
    assert calls == 2
    assert path.read_bytes() == before
    assert json.loads(session.model_dump_json()) == json.loads(before)
