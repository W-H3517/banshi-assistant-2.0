import asyncio
import json
import re
from types import SimpleNamespace

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from sqlalchemy import select

from backend.agent import create_agent
from backend.app import create_app
from backend.chat import ChatService, owned
from backend.config import Settings
from backend.db import Conversation, Database, User
from tests_stage2.pdf_helpers import make_pdf


def login(client, username="admin"):
    response = client.post("/api/login", json={"username": username, "password": "demo12345"})
    assert response.status_code == 200
    return response.json()


def sse(delta, reason="stop"):
    body = {"id": "completion-test", "object": "chat.completion.chunk", "created": 1,
            "model": "deepseek-flash",
            "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
    return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                           content=f"data: {json.dumps(body)}\n\ndata: [DONE]\n\n".encode())


def tool(name, arguments, call_id):
    return sse({"role": "assistant", "tool_calls": [{"index": 0, "id": call_id,
                "type": "function", "function": {"name": name,
                                                  "arguments": json.dumps(arguments)}}]},
               "tool_calls")


def test_login_isolation_upload_and_persistence(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/documents").status_code == 401
        assert client.post("/api/login", json={"username": "admin", "password": "bad"}).status_code == 401
        login(client)
        assert len(client.get("/api/documents").json()) == 3
        document = client.post("/api/documents", files={
            "file": ("测试制度.pdf", make_pdf(["测试制度\n\nA1\n打印凭证必须签名。"]), "application/pdf"),
        }).json()
        assert client.get(f"/api/documents/{document['id']}").json()["filename"] == "测试制度.pdf"
        assert len(client.get("/api/documents?q=凭证 签名").json()) >= 1
        assert client.post("/api/documents", files={"file": ("a.pdf", b"pdf")}).status_code == 422
        assert client.post("/api/documents", files={"file": ("a.md", b"# legacy")}).status_code == 422
        assert client.post("/api/documents", files={"file": ("a.pdf", b"")}).status_code == 422
        assert client.post("/api/documents", files={
            "file": ("a.pdf", b"x" * (20 * 1024 * 1024 + 1)),
        }).status_code == 413
        assert client.post("/api/conversations", headers={"Origin": "https://evil.test"}).status_code == 403
        login(client, "alice")
        conversation = client.post("/api/conversations").json()
        login(client, "bob")
        assert client.get("/api/conversations").json() == []
        path = f"/api/conversations/{conversation['id']}"
        assert client.get(path).status_code == 404
        assert client.post(path + "/turn", json={"prompt": "你好"}).status_code == 404
        assert client.post(path + "/stop").status_code == 404
        assert client.post("/api/documents", files={"file": ("a.md", b"# a")}).status_code == 403
        assert client.get(f"/api/documents/{document['id']}").status_code == 200
        client.post("/api/logout")
        assert client.get("/api/me").status_code == 401
    with TestClient(create_app(settings)) as client:
        login(client, "alice")
        assert client.get(path).status_code == 200
        assert len(client.get("/api/documents").json()) == 4
        assert client.post(path + "/turn", json={"prompt": "你好"}).status_code == 503


@pytest.mark.parametrize("log_mode", ["simple", "detailed"])
def test_agent_tools_question_resume_and_failure(tmp_path, log_mode, capsys):
    requests = []
    state = {"fail": False}

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["thinking"] == {"type": "disabled"}
        assert body["stream"] is True
        assert {item["function"]["name"] for item in body["tools"]} == {
            "search_documents", "ask_question",
        }
        if state["fail"]:
            return httpx2.Response(401, json={"error": {"message": "private-key-body"}})
        if len(requests) == 1:
            return tool("search_documents", {"query": "培训"}, "search1")
        if len(requests) == 2:
            return tool("ask_question", {"question": "是否外地培训？", "options": ["是", "否"]}, "ask1")
        if len(requests) == 4:
            return tool("search_documents", {"query": "外地培训报销材料"}, "search2")
        return sse({"role": "assistant", "content": "请准备培训通知和发票。"})

    clients = []

    def factory(settings, user_id, db):
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        clients.append(client)
        model = OpenAIChatModel(settings.model, provider=DeepSeekProvider(
            api_key="test-key", http_client=client,
        ))
        return create_agent(settings, user_id, db, model=model)

    settings = Settings(data_dir=tmp_path, api_key="test-key", log_mode=log_mode)
    with TestClient(create_app(settings, agent_factory=factory)) as client:
        user = login(client, "alice")
        conversation = client.post("/api/conversations").json()
        path = f"/api/conversations/{conversation['id']}"
        response = client.post(path + "/turn", json={"prompt": "培训报销？test-key"})
        assert response.status_code == 200
        assert '"type": "done"' in response.text, response.text
        paused = client.get(path).json()
        assert paused["status"] == "waiting"
        assert paused["pending"][0]["id"] == "ask1"
        assert paused["transcript"][-1]["sources"]
        assert all(item["user_id"] == user["id"] for item in requests)
        assert "培训" in requests[1]["messages"][-1]["content"]
        tool_content = json.loads(requests[1]["messages"][-1]["content"])
        assert all("content" in doc and "preview" not in doc for doc in tool_content)
        assert any("450" in doc["content"] for doc in tool_content)
        assert all(doc["page"] >= 1 and doc["citation"].endswith(f"#page={doc['page']}")
                   for doc in tool_content)
        assert all(source["page"] >= 1 for source in paused["transcript"][-1]["sources"])
        assert client.post(path + "/turn", json={"answers": {"fake": "是"}}).status_code == 422
        assert client.post(path + "/turn", json={"prompt": "绕过问题"}).status_code == 422
        login(client, "bob")
        assert client.post(path + "/turn", json={"answers": {"ask1": "是"}}).status_code == 404
        login(client, "alice")
    # 等待问题可跨服务重启恢复。
    with TestClient(create_app(settings, agent_factory=factory)) as client:
        login(client, "alice")
        state["fail"] = True
        failure = client.post(path + "/turn", json={"answers": {"ask1": "是"}})
        assert '"type": "error"' in failure.text
        assert "private-key-body" not in failure.text
        assert client.get(path).json()["pending"] == paused["pending"]
        assert client.get(path).json()["transcript"] == paused["transcript"]
        state["fail"] = False
        result = client.post(path + "/turn", json={"answers": {"ask1": "是"}})
        assert '"type": "done"' in result.text, result.text
        restored = client.get(path).json()
        assert restored["status"] == "idle" and not restored["pending"]
        assert restored["transcript"][-1]["text"] == "请准备培训通知和发票。"
        assert restored["transcript"][-1]["sources"]
        assert any(item.get("tool_call_id") == "search2" for item in requests[-1]["messages"])
        assert any(item.get("tool_call_id") == "ask1" and item["content"] == "是"
                   for item in requests[-1]["messages"])
        client.post(path + "/turn", json={"prompt": "再说一遍"})
        assert len(client.get(path).json()["transcript"]) == 6

    log_text = "\n".join(path.read_text(encoding="utf-8") for path in (tmp_path / "logs").glob("*.txt"))
    assert "test-key" not in log_text
    assert "test-key" not in capsys.readouterr().err
    if log_mode == "simple":
        assert "培训报销？" not in log_text
        assert "是否外地培训" not in log_text
        assert "private-key-body" not in log_text
        assert "gen_ai.input.messages" not in log_text
    else:
        spans = [json.JSONDecoder().raw_decode(part)[0] for part in re.split(
            r"(?m)^.* INFO backend.trace \n", log_text,
        )[1:]]
        model_spans = [span for span in spans
                       if span["attributes"].get("gen_ai.operation.name") == "chat"]
        assert len(model_spans) == len(requests)
        assert all("gen_ai.input.messages" in span["attributes"] for span in model_spans)
        assert any("gen_ai.output.messages" in span["attributes"] for span in model_spans)
        # 当前框架把 API 认证异常记录在同一 trace 的 Agent span 上。
        failed = [span for span in spans if span["status"]["status_code"] == "ERROR"]
        assert failed and "401" in failed[0]["status"]["description"]
        assert any(span["context"]["trace_id"] == failed[0]["context"]["trace_id"]
                   for span in model_spans)
        assert "培训报销？[REDACTED]" in log_text
        assert "450" in log_text
        assert "是否外地培训" in log_text
        assert '"conversation_id"' in log_text
        assert conversation["id"] in log_text
        assert "tool_arguments" in log_text or "gen_ai.tool.call.arguments" in log_text
        assert "tool_response" in log_text or "gen_ai.tool.call.result" in log_text
        assert any(
            part.get("id") == "ask1" and part.get("result") == "是"
            for span in model_spans
            for message in span["attributes"]["gen_ai.input.messages"]
            for part in message["parts"]
        )


async def test_stop_and_concurrent_turn_protect_history(tmp_path):
    settings = Settings(data_dir=tmp_path, api_key="test-key")
    db = Database(settings)
    await db.initialize()
    entered = asyncio.Event()

    async def slow_run(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    service = ChatService(db, settings, lambda *args: SimpleNamespace(run=slow_run))
    async with db.sessions() as session:
        user = await session.scalar(select(User).where(User.username == "alice"))
        conversation = Conversation(user_id=user.id, model=settings.model,
                                    transcript=[{"role": "assistant", "text": "已保存"}])
        session.add(conversation)
        await session.commit()
    stream = await service.start(user, conversation.id, "新消息", {})
    await entered.wait()
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await service.start(user, conversation.id, "重复发送", {})
    assert exc.value.status_code == 409
    await service.stop(conversation.id)
    frames = [frame async for frame in stream]
    assert any('"type": "error"' in frame for frame in frames)
    saved = await owned(db, conversation.id, user.id)
    assert saved.status == "interrupted"
    assert saved.transcript == [{"role": "assistant", "text": "已保存"}]
    assert saved.history == []
    await db.engine.dispose()
