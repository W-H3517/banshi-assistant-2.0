import json
from io import BytesIO

import httpx2
from fastapi.testclient import TestClient
from PIL import Image
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from backend.agent import cards_in_history, create_agent
from backend.app import create_app
from backend.config import Settings
from tests.pdf_helpers import make_pdf
from tests.test_app import login, sse, tool
from tests.test_ocr import fake_recognizer


def upload(client, name, raw):
    response = client.post("/api/documents", files={"file": (name, raw)})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_same_content_in_two_files_is_one_hit_and_rule_cards_are_sent_once(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        login(client)
        kb = client.app.state.db.knowledge
        shared = "第一条 会议室借用须提前三个工作日登记。"
        one = upload(client, "甲.pdf", make_pdf([f"甲办法\n{shared}\n本办法自发布之日起施行。"]))
        two = upload(client, "乙.pdf", make_pdf([f"乙手册\n{shared}\n第二条 钥匙用后归还前台。"]))

        hits = client.portal.call(kb.search, "会议室借用提前几天登记")
        same = [h for h in hits if "提前三个工作日" in h["content"]]
        assert len(same) == 1 and {same[0]["id"], same[0]["also_in"][0]["id"]} == {one, two}
        other = same[0]["also_in"][0]
        assert other["citation"] == f"/api/documents/{other['id']}#page=1" and other["node_id"]
        # 让出的名额留给其他内容。
        assert len({h["node_id"] for h in hits}) == len(hits)

        sent = set()
        first = client.portal.call(kb.search_bundle, ["会议室借用提前几天登记"], None, None, sent)
        item = next(i for i in first["items"] if "提前三个工作日" in i["content"])
        assert item["also_in"] and first["documents"][one] == "甲办法" and first["documents"][two] == "乙手册"
        # 交给模型的条目不带模型用不到的 ID 和重复的来源字段，默认状态不输出。
        assert not {"chunk_id", "matched_chunk_ids", "unit_id", "source", "title", "page",
                    "boundary_status", "matched_queries"} & set(item)
        assert item["path"] == "第一条" and item["citation"].endswith("#page=1")
        assert {c["id"] for c in first["rule_cards"]} == {i["id"] for i in first["items"]} == sent
        assert "rule_cards_sent_before" not in first
        # 同一段对话再次命中这些文件时不再重发规则卡，只说明前文已提供。
        again = client.portal.call(kb.search_bundle, ["会议室钥匙归还", "借用登记"], None, None, sent)
        assert again["rule_cards"] == [] and again["rule_cards_sent_before"]
        assert all("matched_queries" in i for i in again["items"])
        # 不传已发送集合时（如独立调用）每次都附带。
        assert client.portal.call(kb.search_bundle, ["借用登记"])["rule_cards"]


def test_scan_warning_only_follows_content_from_scanned_pages(tmp_path, monkeypatch):
    fake_recognizer(monkeypatch)
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        login(client)
        kb = client.app.state.db.knowledge
        # 两页扫描页之后接一页文字页；文字页以新的编号项开头，是独立的阅读单元。
        output = BytesIO()
        canvas = Canvas(output, pagesize=(600, 800))
        for _ in range(2):
            canvas.drawImage(ImageReader(Image.new("RGB", (20, 20), "white")), 0, 0, 600, 800)
            canvas.showPage()
        canvas.setFont("Helvetica", 12)
        canvas.drawString(60, 700, "3.Native text page stays selectable and is never sent to OCR.")
        canvas.save()
        document = upload(client, "混合.pdf", output.getvalue())
        native = next(h for h in client.portal.call(kb.search, "Native text selectable", document)
                      if "Native text" in h["content"])
        assert native["pages"] == [3] and native["warnings"] == []
        scanned = next(h for h in client.portal.call(kb.search, "完整材料", document)
                       if "完整材料" in h["content"])
        assert any("扫描" in w for w in scanned["warnings"])
        read = client.portal.call(kb.read, native["node_id"])
        assert read["items"][0]["warnings"] == []
        assert any("扫描" in w for w in client.portal.call(kb.read, scanned["node_id"])["items"][0]["warnings"])


def test_history_scan_finds_documents_whose_rule_cards_were_sent():
    history = [ModelRequest(parts=[
        ToolReturnPart(tool_name="search_documents", tool_call_id="a",
                       content={"items": [], "rule_cards": [{"id": "doc-1", "rules": []}]}),
        ToolReturnPart(tool_name="read_document_content", tool_call_id="b",
                       content={"items": [], "rule_cards": [{"id": "ignored"}]}),
        ToolReturnPart(tool_name="search_documents", tool_call_id="c", content="参数错误"),
    ])]
    assert cards_in_history(history) == {"doc-1"}


def test_two_malformed_calls_recover_and_narration_is_a_separate_paragraph(tmp_path):
    requests, clients = [], []

    def call(arguments, content=None):
        delta = {"role": "assistant", "tool_calls": [{
            "index": 0, "id": f"search{len(requests)}", "type": "function",
            "function": {"name": "search_documents", "arguments": arguments}}]}
        return sse(delta | ({"content": content} if content else {}), "tool_calls")

    def handler(request):
        requests.append(json.loads(request.content))
        if len(requests) <= 2:
            # 实际出现过的错误：字符串没有加引号。同一轮内连续两次也应能纠正。
            return call('{"queries": [差旅补助标准]}')
        if len(requests) == 3:
            return call(json.dumps({"queries": ["差旅补助标准"]}), "我先查一下相关制度。")
        if len(requests) == 4:
            # 第二次搜索命中同样的文件，规则卡不再重发。
            first = json.loads(requests[3]["messages"][-1]["content"])
            assert first["rule_cards"] and "rule_cards_sent_before" not in first
            return tool("search_documents", {"queries": ["住宿限额"]}, "search-again")
        second = json.loads(requests[4]["messages"][-1]["content"])
        assert second["rule_cards"] == [] and second["rule_cards_sent_before"]
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
        response = client.post(path + "/turn", json={"prompt": "差旅补助多少"})
        assert '"type": "done"' in response.text, response.text
        saved = client.get(path).json()
        assert saved["status"] == "idle" and len(requests) == 5
        assert saved["transcript"][-1]["text"] == "我先查一下相关制度。\n\n模拟标准为450元。"
        assert '"text": "\\n\\n模拟标准为450元。"' in response.text
        assert saved["transcript"][-1]["sources"]
        for transport in clients:
            client.portal.call(transport.aclose)
