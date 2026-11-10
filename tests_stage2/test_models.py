import json
from types import SimpleNamespace

import httpx2
import pytest
from pydantic_ai import DeferredToolRequests

from backend.agent import create_agent
from backend.config import Settings
from backend.models import create_model, model_settings


def test_provider_config_keeps_keys_separate_and_existing_conversations(monkeypatch):
    names = ["MODEL_PROVIDER", "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "OPENAI_API_KEY",
             "OPENAI_MODEL", "OPENAI_API", "OPENAI_REASONING_EFFORT"]
    for name in names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("backend.config.dotenv_values", lambda *args, **kwargs: {
        "DEEPSEEK_API_KEY": "deepseek-test", "OPENAI_API_KEY": "openai-test",
        "OPENAI_MODEL": "gpt-6-astra", "OPENAI_REASONING_EFFORT": "low"})
    previous = Settings.load()
    assert previous.api_key == "deepseek-test" and previous.conversation_model == "deepseek-flash"
    monkeypatch.setenv("MODEL_PROVIDER", "openai")
    current = Settings.load()
    assert current.api_key == "openai-test" and current.model == "gpt-6-astra"
    assert current.conversation_model == "openai:responses:gpt-6-astra"
    assert current.reasoning_effort == "low"
    assert "extra_body" not in model_settings(current, "user")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert Settings.load().api_key == ""
    monkeypatch.setenv("OPENAI_MODEL", "")
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        Settings.load()


@pytest.mark.parametrize("api", ["responses", "chat_completions"])
@pytest.mark.parametrize("tool_name", ["search_documents", "ask_question"])
async def test_openai_tool_roundtrip_has_no_deepseek_parameters(api, tool_name):
    settings = Settings(provider="openai", api_key="mock-key", model="gpt-6-astra" if api == "responses" else "gpt-5.6-terra",
                        openai_api=api, reasoning_effort="low" if api == "responses" else "none")
    requests = []
    calls = []

    async def search(query, document_id, node_id):
        calls.append(query)
        return {"items": [], "rule_cards": [], "queries": query}

    def handler(request):
        assert request.url.host == "api.openai.com"
        assert request.url.path == "/v1/" + ("responses" if api == "responses" else "chat/completions")
        body = json.loads(request.content)
        requests.append(body)
        assert "thinking" not in body and "user_id" not in body and body["store"] is False
        schemas = body["tools"] if api == "responses" else [t["function"] for t in body["tools"]]
        assert {t["name"] for t in schemas} == {
            "search_documents", "locate_document_structure", "read_document_content", "ask_question"}
        assert all(t.get("strict", False) is False for t in schemas)
        arguments = {"query": ["住宿限额", "凭据报销"]} if tool_name == "search_documents" else {"question": "是否已获审批人同意？"}
        if len(requests) == 2:
            returned = body["input"] if api == "responses" else body["messages"]
            assert "rule_cards" in json.dumps(returned)
        if api == "responses":
            assert body["reasoning"]["effort"] == settings.reasoning_effort
            output = [{"id": "fc_1", "type": "function_call", "status": "completed",
                       "call_id": "call_1", "name": tool_name, "arguments": json.dumps(arguments)}] if len(requests) == 1 else [
                {"id": "msg_1", "type": "message", "status": "completed", "role": "assistant",
                 "content": [{"type": "output_text", "text": "完成", "annotations": []}]}]
            payload = {"id": f"resp_{len(requests)}", "object": "response", "created_at": 1,
                       "status": "completed", "model": settings.model, "output": output,
                       "parallel_tool_calls": True, "tool_choice": "auto", "tools": []}
        else:
            assert body["reasoning_effort"] == settings.reasoning_effort
            message = {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function",
                       "function": {"name": tool_name, "arguments": json.dumps(arguments)}}]} if len(requests) == 1 else {
                "role": "assistant", "content": "完成"}
            payload = {"id": f"chat_{len(requests)}", "object": "chat.completion", "created": 1, "model": settings.model,
                       "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if len(requests) == 1 else "stop"}]}
        return httpx2.Response(200, json=payload)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        model = create_model(settings, http_client=client)
        agent = create_agent(settings, "user", SimpleNamespace(knowledge=SimpleNamespace(search_bundle=search)), model=model)
        result = await agent.run("检查工具流转")
    if tool_name == "search_documents":
        assert result.output == "完成" and calls == [["住宿限额", "凭据报销"]]
        assert len(requests) == 2
    else:
        assert isinstance(result.output, DeferredToolRequests)
        assert result.output.calls[0].args_as_dict()["question"] == "是否已获审批人同意？"
        assert not calls and len(requests) == 1
