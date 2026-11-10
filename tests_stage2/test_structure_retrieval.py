import json
from io import BytesIO

import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from backend.agent import create_agent
from backend.app import create_app
from backend.config import Settings
from backend.indexing import assign_context
from backend.structure import build_structure, get_node, read_nodes, reading_content
from tests_stage2.pdf_helpers import make_pdf
from tests_stage2.test_app import login, sse, tool


@pytest.fixture
def app_client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        login(client)
        yield client


def upload(client, raw, filename="结构测试.pdf"):
    response = client.post("/api/documents", files={"file": (filename, raw)})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_long_article_grouping_scoped_search_and_lossless_read(app_client):
    client = app_client
    kb = client.app.state.db.knowledge
    raw = make_pdf(["结构测试\n第一章 申请\n第一条 " + "报销材料须经审核并保存完整凭证。" * 440,
                    "最终例外：应急采购上限为87654321元。\n第二条 不得重复领取。"])
    document_id = upload(client, raw)
    hits = client.portal.call(kb.search, "87654321", document_id)
    target = next(h for h in hits if "87654321" in h["content"])
    assert target["article_id"] and not target["excerpt_complete"]
    assert len({h["node_id"] for h in hits}) == len(hits)
    repeated = client.portal.call(kb.search, "报销材料审核", document_id, target["node_id"])
    assert len(repeated) == 1 and len(repeated[0]["matched_chunk_ids"]) > 1
    assert "文件：" not in target["content"]
    located = client.portal.call(kb.locate, "第一条", document_id)
    assert located["items"][0]["node_id"] == target["article_id"]
    first = client.portal.call(kb.read, target["chunk_id"], "article")
    assert not first["pagination_complete"] and first["next_cursor"]
    items = first["items"][:]
    result = first
    while not result["pagination_complete"]:
        result = client.portal.call(kb.read, target["chunk_id"], "article", 1, result["next_cursor"])
        items.extend(result["items"])
    snapshots = client.portal.call(kb.snapshots, document_id)
    structure = snapshots[document_id][0]["structure"]
    original = reading_content(structure, get_node(structure, target["node_id"]))["content"]
    assert "".join(i["content"] for i in items) == original
    assert "最终例外" in original and "第二条" not in original
    assert len({p for i in items for p in i["pages"]}) > 1
    with pytest.raises(ValueError, match="游标"):
        client.portal.call(kb.read, target["chunk_id"], "node", 1, first["next_cursor"])
    with pytest.raises(ValueError, match="游标"):
        client.portal.call(kb.read, target["chunk_id"], "article", 1, "bad-cursor")
    assert client.delete(f"/api/documents/{document_id}").status_code == 200
    new_id = upload(client, raw)
    assert new_id != document_id
    with pytest.raises(ValueError, match="不存在"):
        client.portal.call(kb.read, target["node_id"])
    with pytest.raises(ValueError, match="不存在"):
        client.portal.call(kb.read, target["chunk_id"])


def test_duplicate_labels_title_only_semantics_and_directory(app_client, monkeypatch):
    client = app_client
    kb = client.app.state.db.knowledge
    one = upload(client, make_pdf([("重名办法\n第一章 产假\n第一条 不得超过五日。\n"
                                  "第二章 探亲\n第一条 不得超过三日。")]), "甲.pdf")
    two = upload(client, make_pdf(["重名办法\n第一章 产假\n第一条 不得超过七日。"]), "乙.pdf")
    assert {d["id"] for d in client.get("/api/documents?q=重名办法").json()[:2]} == {one, two}
    found = client.portal.call(kb.locate, "重名办法")
    assert {n["id"] for n in found["items"]} == {one, two}
    monkeypatch.setattr("backend.knowledge.LOCATE_LIMIT", 1)
    page_one = client.portal.call(kb.locate, "重名办法")
    assert not page_one["complete"]
    page_two = client.portal.call(kb.locate, "重名办法", None, None, page_one["next_cursor"])
    assert page_two["complete"] and page_one["items"][0]["id"] != page_two["items"][0]["id"]
    monkeypatch.setattr("backend.knowledge.LOCATE_LIMIT", 30)
    articles = client.portal.call(kb.locate, "第一条", one)["items"]
    assert len(articles) == 2 and articles[0]["node_id"] != articles[1]["node_id"]
    chapter = client.portal.call(kb.locate, "产假", one)["items"][0]
    children = client.portal.call(kb.locate, "", one, chapter["node_id"])["items"]
    assert len(children) == 1 and children[0]["label"] == "第一条"
    read = client.portal.call(kb.read, chapter["node_id"])
    text = "".join(i["content"] for i in read["items"])
    assert "五日" in text and "三日" not in text and "七日" not in text
    scoped = client.portal.call(kb.search, "超过", one, chapter["node_id"])
    assert scoped and all("五日" in h["content"] for h in scoped)
    with pytest.raises(ValueError, match="不存在"):
        client.portal.call(kb.search, "超过", two, chapter["node_id"])


def test_nested_article_and_neighbors_do_not_cross_structure():
    texts = ["第一章总则", "第一条", "（一）申请材料", "1.凭证。应当完整。",
             "补充证明。", "不得代领。", "第二条 不得重复报销。", "第二章其他", "单独事项。"]
    sections = [{"id": i + 1, "kind": "text", "title": text, "content": text,
                 "pages": [1], "source_spans": [{"start": 0, "end": len(text), "page": 1}]}
                for i, text in enumerate(texts)]
    assign_context(sections, "测试")
    structure = build_structure({"sections": sections}, {"chunks": []}, "测试", "测试.pdf")
    target = next(n for n in structure["nodes"] if reading_content(structure, n)["content"] == "补充证明。")
    _, neighbors = read_nodes(structure, target, "neighbors", 3)
    assert [n["content"] for n in neighbors] == texts[3:6]
    article, parts = read_nodes(structure, target, "article", 1)
    assert article["label"] == "第一条"
    assert [n["content"] for n in parts] == texts[1:6]
    assert all("第二条" not in n["content"] for n in parts)


def test_table_rows_keep_fields_and_read_separately(app_client):
    stream = BytesIO()
    table = Table([["Region", "Limit"], ["City A", "500"], ["City B", "400"]])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    SimpleDocTemplate(stream).build([table])
    client = app_client
    document_id = upload(client, stream.getvalue(), "table.pdf")
    kb = client.app.state.db.knowledge
    hits = client.portal.call(kb.search, "500", document_id)
    target = next(h for h in hits if "500" in h["content"])
    assert "Limit" in target["content"] and "Region" in target["content"]
    assert "制度：" not in target["content"]
    result = client.portal.call(kb.read, target["node_id"])
    assert result["pagination_complete"] and len(result["items"]) == 1
    assert "400" not in result["items"][0]["content"]
    assert result["items"][0]["header_pages"] == [1]
    assert result["items"][0]["warnings"]
    assert "header_cells" not in result["items"][0]["table"]
    geometry = client.portal.call(kb.read, target["node_id"], "node", 1, None, None, True)
    assert geometry["items"][0]["table"]["header_cells"]
    assert geometry["items"][0]["table"]["fields"][0]["bbox"]
    assert len(json.dumps(result)) < len(json.dumps(geometry))


def test_agent_locate_read_search_and_sources(tmp_path):
    requests, clients = [], []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            names = {t["function"]["name"] for t in body["tools"]}
            assert names == {"ask_question", "search_documents", "locate_document_structure",
                             "read_document_content"}
            return tool("locate_document_structure", {"query": "差旅"}, "locate1")
        previous = json.loads(body["messages"][-1]["content"])
        if len(requests) == 2:
            return tool("read_document_content", {"target_id": previous["items"][0]["node_id"]}, "read1")
        if len(requests) == 3:
            assert previous["pagination_complete"] and any("450" in i["content"] for i in previous["items"])
            return tool("search_documents", {"query": "酒店预算上限"}, "search1")
        assert any("450" in i["content"] for i in previous["items"])
        assert "rule_cards" in previous
        return sse({"role": "assistant", "content": "模拟标准为450元。"})

    def factory(settings, user_id, db):
        transport = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        clients.append(transport)
        model = OpenAIChatModel(settings.model, provider=DeepSeekProvider(api_key="test", http_client=transport))
        return create_agent(settings, user_id, db, model=model)

    with TestClient(create_app(Settings(data_dir=tmp_path, api_key="test"), agent_factory=factory)) as client:
        login(client, "alice")
        conversation = client.post("/api/conversations").json()
        path = f"/api/conversations/{conversation['id']}"
        response = client.post(path + "/turn", json={"prompt": "住宿限额"})
        assert '"type": "done"' in response.text, response.text
        message = client.get(path).json()["transcript"][-1]
        assert message["sources"] and all(t["status"] == "done" for t in message["tools"])
        assert [t["name"] for t in message["tools"]] == [
            "locate_document_structure", "read_document_content", "search_documents"]
        for transport in clients:
            client.portal.call(transport.aclose)


def test_search_rule_card_page_is_added_to_chat_sources(tmp_path):
    requests, clients = [], []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return tool("search_documents", {"query": ["公务平台预订机票", "商业代理预订机票"]}, "search-rules")
        result = json.loads(body["messages"][-1]["content"])
        assert result["items"]
        assert any("以本通知为准" in r["content"] for c in result["rule_cards"] for r in c["rules"])
        return sse({"role": "assistant", "content": "已检查规则卡。"})

    def factory(settings, user_id, db):
        transport = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        clients.append(transport)
        model = OpenAIChatModel(settings.model, provider=DeepSeekProvider(api_key="test", http_client=transport))
        return create_agent(settings, user_id, db, model=model)

    with TestClient(create_app(Settings(data_dir=tmp_path, api_key="test"), agent_factory=factory)) as client:
        login(client)
        doc = upload(client, make_pdf([
            "票务通知\n第一条 公务机票通过公务平台预订，不得通过商业代理预订。",
            "本通知自发布之日起施行。此前规定与本通知不一致的，以本通知为准。",
        ]), "票务通知.pdf")
        conversation = client.post('/api/conversations').json()
        path = f"/api/conversations/{conversation['id']}"
        result = client.post(path + '/turn', json={'prompt': '机票在哪预订？'})
        assert '"type": "done"' in result.text
        sources = client.get(path).json()['transcript'][-1]['sources']
        assert any(s['id'] == doc and s['page'] == 2 for s in sources)
        assert len({(s['id'], s['page']) for s in sources}) == len(sources)
        for transport in clients:
            client.portal.call(transport.aclose)


@pytest.mark.parametrize("arguments", ['{"query": ["差旅补助"]}', '{"query": 差旅补助}'])
def test_invalid_search_arguments_retry_without_becoming_sources(tmp_path, arguments):
    requests, clients = [], []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            query = body["tools"][0]["function"]["parameters"]["properties"]["query"]
            assert query["description"] and query["anyOf"][1]["minItems"] == 2
            return sse({"role": "assistant", "tool_calls": [{"index": 0, "id": "invalid-search",
                        "type": "function", "function": {"name": "search_documents",
                                                           "arguments": arguments}}]}, "tool_calls")
        if len(requests) == 2:
            feedback = body["messages"][-1]["content"].lower()
            assert "validation" in feedback or "json_invalid" in feedback
            return tool("search_documents", {"query": "差旅补助"}, "corrected-search")
        assert "items" in json.loads(body["messages"][-1]["content"])
        return sse({"role": "assistant", "content": "参数纠正后检索完成。"})

    def factory(settings, user_id, db):
        transport = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        clients.append(transport)
        model = OpenAIChatModel(settings.model, provider=DeepSeekProvider(api_key="test", http_client=transport))
        return create_agent(settings, user_id, db, model=model)

    with TestClient(create_app(Settings(data_dir=tmp_path, api_key="test"), agent_factory=factory)) as client:
        login(client)
        conversation = client.post("/api/conversations").json()
        path = f"/api/conversations/{conversation['id']}"
        result = client.post(path + "/turn", json={"prompt": "差旅补助"})
        assert '"type": "done"' in result.text, result.text
        assert len(requests) == 3
        assert client.get(path).json()["status"] == "idle"
        for transport in clients:
            client.portal.call(transport.aclose)
