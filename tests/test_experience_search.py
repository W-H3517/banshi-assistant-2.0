import json

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from sqlalchemy import delete, func, select

from backend.agent import create_agent
from backend.app import create_app
from backend.config import Settings
from backend.db import ExperienceChunk
from tests.test_app import login, sse, tool
from tests.test_experiences import review


def submit(client, text="员工保密原文", tags=None, send=True):
    return client.post("/api/experiences", json={
        "text": text, "tags": tags or ["采购"], "submit": send}).json()["id"]


def test_experience_lifecycle_search_privacy_and_backfill(tmp_path):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        author = login(client)
        kb = client.app.state.db.knowledge
        draft = submit(client, send=False)
        pending = submit(client)
        rejected = submit(client)
        verifying = submit(client)
        review(client, rejected, action="reject")
        review(client, verifying, action="verify")
        experience_id = submit(client)
        content = "采购打印纸先向行政领取比价单。"
        assert review(client, experience_id, action="publish", content=content,
                      tags=["采购", "专属标签"], conflict=True,
                      conflict_note="制度未要求比价单").status_code == 200

        def search(query="采购打印纸", document_id=None, node_id=None):
            return client.portal.call(kb.search_bundle, [query], document_id, node_id)

        item, = search()["experiences"]
        assert item["id"] == experience_id and item["content"] == content
        assert item["conflict_note"] == "制度未要求比价单" and "partial" not in item
        assert not {draft, pending, rejected, verifying} & {item["id"]}
        for secret in (author["id"], author["name"], "员工保密原文", "author_id", "original"):
            assert secret not in json.dumps(item, ensure_ascii=False)
        assert search("专属标签")["experiences"]
        assert search("火星探测器轨道设计")["experiences"] == []
        doc = client.get("/api/documents").json()[0]["id"]
        assert search(document_id=doc)["experiences"] == [item]
        nodes = client.portal.call(kb.locate, "", doc)
        # 定位返回的文档根节点也是有效制度范围。
        node = nodes["items"][0]["node_id"]
        assert search(document_id=doc, node_id=node)["experiences"] == [item]

        assert review(client, experience_id, action="withdraw").status_code == 200
        assert search()["experiences"] == []
        assert client.get(item["citation"]).status_code == 404  # 提交人也不能回退到原文。
        with pytest.raises(ValueError, match="下架"):
            client.portal.call(kb.read_experience, experience_id)
        assert review(client, experience_id, action="publish").status_code == 200
        assert search()["experiences"]
        updated = "采购打印纸现在使用电子比价单，无需领取纸质表。"
        assert review(client, experience_id, action="save", content=updated).status_code == 200
        assert search()["experiences"][0]["content"] == updated

        async def remove_derived():
            async with client.app.state.db.sessions.begin() as session:
                await session.execute(delete(ExperienceChunk))
        client.portal.call(remove_derived)
    with TestClient(create_app(settings)) as client:
        kb = client.app.state.db.knowledge
        assert client.portal.call(kb.search_bundle, ["采购"])["experiences"][0]["content"] == updated

        async def count():
            async with client.app.state.db.sessions() as session:
                return await session.scalar(select(func.count()).select_from(ExperienceChunk))
        before = client.portal.call(count)
        client.portal.call(kb.backfill_experiences)
        assert client.portal.call(count) == before


def test_long_experience_mock_agent_sources_and_reading(tmp_path):
    requests = []
    experience_id = ""
    content = "采购报销先准备电子比价单。" * 150

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return tool("search_documents", {"queries": ["采购报销"]}, "search")
        if len(requests) == 2:
            bundle = json.loads(body["messages"][-1]["content"])
            assert bundle["items"] and bundle["experiences"]
            item, = bundle["experiences"]
            assert item["partial"] and len(item["content"]) < len(content)
            return tool("read_document_content", {"target_id": experience_id}, "read")
        read = json.loads(body["messages"][-1]["content"])
        assert read["experiences"][0]["content"] == content
        assert "partial" not in read["experiences"][0]
        return sse({"role": "assistant", "content": (
            f"请准备比价单。[某同事的经验](/api/experiences/{experience_id}/published)")})

    def factory(settings, user_id, db):
        http = httpx2.AsyncClient(transport=httpx2.MockTransport(respond))
        model = OpenAIChatModel("deepseek-flash", provider=DeepSeekProvider(
            api_key="test-key", http_client=http))
        return create_agent(settings, user_id, db, model=model)

    with TestClient(create_app(Settings(data_dir=tmp_path, api_key="test-key"),
                               agent_factory=factory)) as client:
        author = login(client)
        experience_id = submit(client)
        assert review(client, experience_id, action="publish", content=content).status_code == 200
        conversation = client.post("/api/conversations").json()
        path = f"/api/conversations/{conversation['id']}"
        response = client.post(path + "/turn", json={"prompt": "采购报销"})
        assert '"type": "done"' in response.text, response.text
        sources = client.get(path).json()["transcript"][-1]["sources"]
        experiences = [s for s in sources if s.get("kind") == "experience"]
        assert len(experiences) == 1 and experiences[0]["id"] == experience_id
        assert any(s.get("kind") == "document" for s in sources)
        for secret in (author["id"], author["name"], "员工保密原文"):
            assert secret not in json.dumps(sources, ensure_ascii=False)
            for request in requests[1:]:
                returns = [m for m in request["messages"] if m["role"] == "tool"]
                assert secret not in json.dumps(returns, ensure_ascii=False)
        assert review(client, experience_id, action="withdraw").status_code == 200
        assert client.get(f"/api/experiences/{experience_id}/published").status_code == 404
        assert client.get(path).json()["transcript"][-1]["sources"] == sources


def test_experience_limits_dedup_and_transaction_rollback(tmp_path, monkeypatch):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        login(client)
        kb = client.app.state.db.knowledge
        ids = [submit(client) for _ in range(5)]
        for i, experience_id in enumerate(ids):
            assert review(client, experience_id, action="publish",
                          content=f"采购打印纸第{i}项说明：请填写电子比价单。").status_code == 200
        one = client.portal.call(kb.search_bundle, ["采购打印纸"])["experiences"]
        assert len(one) == 3 and len({item["id"] for item in one}) == 3
        many = client.portal.call(kb.search_bundle, ["采购打印纸", "电子比价单"])["experiences"]
        assert len(many) <= 6
        assert len({(i["id"], i["content"]) for i in many}) == len(many)
        before = client.portal.call(kb.read_experience, ids[0])

        def failed_embedding(chunks):
            raise RuntimeError("模拟本地向量失败")
        monkeypatch.setattr(kb.embeddings, "passages", failed_embedding)
        with pytest.raises(RuntimeError, match="向量失败"):
            review(client, ids[0], action="save", content="不应写入的新内容")
        assert client.portal.call(kb.read_experience, ids[0]) == before
        assert client.portal.call(kb.search_bundle, ["采购打印纸"])["experiences"] == one
