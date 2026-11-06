from io import BytesIO
from itertools import pairwise

from fastapi.testclient import TestClient
from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from backend.app import create_app
from backend.config import Settings
from backend.document_preview import convert_pdf, preview_document
from backend.embeddings import LocalEmbeddings
from tests_stage2.pdf_helpers import make_pdf
from tests_stage2.test_app import login


def test_cross_page_articles_and_markdown_preserve_all_paragraphs():
    raw = make_pdf([
        "示例管理办法\n第一章 总则\n第一条 出差人员应提交住宿\n发票，并按照以下条件办理，",
        "经批准后可以报销。\n另有特殊限制。\n第二条 不得重复报销。\n附件：1.标准表",
    ])
    result = convert_pdf(raw)
    articles = [s for s in result["sections"] if s["kind"] == "article"]
    assert len(articles) == 2
    assert articles[0]["pages"] == [1, 2]
    assert "住宿发票" in articles[0]["content"]
    assert "经批准后可以报销。\n\n另有特殊限制。" in articles[0]["content"]
    assert "另有特殊限制。" in result["markdown"]
    assert "### 第二条" in result["markdown"]
    assert articles[1]["pages"] == [2]
    assert "附件" not in articles[1]["content"]
    assert any(s["title"] == "附件目录" for s in result["sections"])
    for section in articles:
        spans = section["source_spans"]
        assert spans[0]["start"] == 0
        assert spans[-1]["end"] == len(section["content"])
        assert all(a["end"] == b["start"] for a, b in pairwise(spans))


def test_only_margin_page_numbers_are_removed():
    stream = BytesIO()
    canvas = Canvas(stream)
    for number in (1, 2):
        canvas.drawString(80, 500, "Policy body 123")
        canvas.drawString(280, 25, f"- {number} -")
        canvas.showPage()
    canvas.save()
    result = convert_pdf(stream.getvalue())
    assert "123" in result["markdown"]
    assert [r["text"] for r in result["removed_lines"]] == ["- 1 -", "- 2 -"]


def test_table_preserves_cells_without_duplicating_text_or_guessing_headers():
    stream = BytesIO()
    table = Table([["Region", "Limit"], ["City A", "500"], ["City B", "400"]])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    SimpleDocTemplate(stream).build([table])
    result = convert_pdf(stream.getvalue())
    tables = [s for s in result["sections"] if s["kind"] == "table"]
    assert len(tables) == 1
    assert "| Region | Limit |" in result["markdown"]
    assert result["markdown"].count("City A") == 1
    assert any(w["page"] == 1 for w in result["warnings"])


def test_preview_chunks_keep_page_mapping_and_model_budget():
    embeddings = LocalEmbeddings(Settings().model_cache_dir)
    raw = make_pdf(["测试制度\n第一条 " + "提交完整证明材料，经审批后办理。" * 20,
                    "跨页补充限制不得遗漏。\n第二条 另一项规定。"])
    result = preview_document(raw, "测试制度", embeddings)
    assert any(c["pages"] == [1, 2] for c in result["chunks"])
    assert all(c["token_count"] <= 512 for c in result["chunks"])
    assert any("跨页补充限制" in c["content"] for c in result["chunks"])
    for chunk in result["chunks"]:
        parent = result["sections"][chunk["section_id"] - 1]
        assert chunk["content"] == parent["content"][chunk["start"]:chunk["end"]]
        assert set(chunk["pages"]).issubset(parent["pages"])


def test_preview_permissions_and_no_index_mutations(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        login(client)
        docs = client.get("/api/documents").json()
        document = docs[0]
        path = f"/api/documents/{document['id']}/conversion-preview"
        conversation = client.post("/api/conversations").json()
        first = client.get(path)
        assert first.status_code == 200
        result = first.json()
        assert result["active_for_search"] is False
        assert len(result["current_chunks"]) == document["chunk_count"]
        assert result["markdown"] and result["chunks"]
        trace_path = f"/api/documents/{document['id']}/reference-preview?section_id=1"
        trace = client.get(trace_path)
        assert trace.status_code == 200
        assert trace.json()["active_for_search"] is False
        assert trace.json()["document_count"] == 3
        assert client.get(trace_path.replace("section_id=1", "section_id=999")).status_code == 422
        assert client.get(path).json() == result
        assert client.get("/api/documents").json() == docs
        assert client.get(f"/api/conversations/{conversation['id']}").json() == conversation
        assert client.get("/api/documents/missing/conversion-preview").status_code == 404
        login(client, "alice")
        assert client.get(path).status_code == 403
        assert client.get(trace_path).status_code == 403
        client.post("/api/logout")
        assert client.get(path).status_code == 401
        assert client.get(trace_path).status_code == 401
