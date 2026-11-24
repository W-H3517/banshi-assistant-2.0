from io import BytesIO
from itertools import pairwise

from fastapi.testclient import TestClient
from reportlab.lib import colors
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from backend.app import create_app
from backend.config import Settings
from backend.embeddings import LocalEmbeddings
from backend.indexing import INDEX_VERSION, prepare_document
from backend.parsing import parse_pdf
from tests.pdf_helpers import make_pdf
from tests.test_app import login


def indexed(raw, embeddings):
    return prepare_document(raw, "example.pdf", embeddings, None)[1]


def test_cross_page_articles_preserve_all_paragraphs():
    raw = make_pdf([
        "示例管理办法\n第一章 总则\n第一条 出差人员应提交住宿\n发票，并按照以下条件办理，",
        "经批准后可以报销。\n另有特殊限制。\n第二条 不得重复报销。\n附件：1.标准表",
    ])
    result = parse_pdf(raw)
    articles = [s for s in result["sections"] if s["kind"] == "article"]
    assert len(articles) == 2
    assert articles[0]["pages"] == [1, 2]
    assert "住宿发票" in articles[0]["content"]
    assert "经批准后可以报销。\n\n另有特殊限制。" in articles[0]["content"]
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
    result = parse_pdf(stream.getvalue())
    assert "123" in result["sections"][0]["content"]
    assert [r["text"] for r in result["removed_lines"]] == ["- 1 -", "- 2 -"]


def test_table_preserves_cells_without_duplicating_text_or_guessing_headers():
    stream = BytesIO()
    table = Table([["Region", "Limit"], ["City A", "500"], ["City B", "400"]])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    SimpleDocTemplate(stream).build([table])
    result = parse_pdf(stream.getvalue())
    tables = [s for s in result["sections"] if s["kind"] == "table"]
    assert len(tables) == 1
    assert "Region | Limit" in tables[0]["content"]
    assert tables[0]["content"].count("City A") == 1


def test_preview_chunks_keep_page_mapping_and_model_budget():
    embeddings = LocalEmbeddings(Settings().model_cache_dir)
    raw = make_pdf(["测试制度\n第一条 " + "提交完整证明材料，经审批后办理。" * 20,
                    "跨页补充限制不得遗漏。\n第二条 另一项规定。"])
    result = indexed(raw, embeddings)
    assert any(c["pages"] == [1, 2] for c in result["chunks"])
    assert all(c["token_count"] <= 512 for c in result["chunks"])
    assert any("跨页补充限制" in c["content"] for c in result["chunks"])
    units = {u["id"]: u for u in result["units"]}
    for chunk in result["chunks"]:
        assert chunk["location_status"] == "complete"
        assert {r["page"] for r in chunk["regions"]} == set(chunk["pages"])
        for rect in chunk["regions"]:
            assert 0 <= rect["x"] < rect["x"] + rect["width"] <= 1
            assert 0 <= rect["y"] < rect["y"] + rect["height"] <= 1
        parent = units[chunk["unit_id"]]
        assert chunk["content"] == parent["content"][chunk["start"]:chunk["end"]]
        assert set(chunk["pages"]).issubset({s["page"] for s in parent["spans"]})


def test_preview_permissions_and_no_index_mutations(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        login(client)
        docs = client.get("/api/documents").json()
        document = docs[0]
        path = f"/api/documents/{document['id']}/chunk-preview"
        conversation = client.post("/api/conversations").json()
        first = client.get(path)
        assert first.status_code == 200
        result = first.json()
        assert result["version"] == INDEX_VERSION
        assert "active_for_search" not in result
        assert result["pages"] and result["chunks"]
        assert "current_chunks" not in result and "markdown" not in result
        # 经验接口以子路由方式挂载，子路由对象本身没有 path。
        paths = {route.path for route in client.app.routes if hasattr(route, "path")}
        assert "/api/documents/{document_id}/conversion-preview" not in paths
        assert "/api/documents/{document_id}/reference-preview" not in paths
        original = client.get(f"/api/documents/{document['id']}/file").content
        assert client.get(path).json() == result
        assert client.get("/api/documents").json() == docs
        assert client.get(f"/api/conversations/{conversation['id']}").json() == conversation
        assert client.get("/api/documents/missing/chunk-preview").status_code == 404
        assert client.get(f"/api/documents/{document['id']}/file").content == original
        # 普通员工也能看切分预览，但不含诊断信息。
        login(client, "alice")
        reader = client.get(path).json()
        assert "version" not in reader and "removed_line_count" not in reader
        assert [c["content"] for c in reader["chunks"]] == [c["content"] for c in result["chunks"]]
        assert all({"node_id", "parent_id", "regions", "pages"} <= set(c) for c in reader["chunks"])
        assert not {"token_count", "boundary_status", "location_status", "warnings"} & set(reader["chunks"][0])
        assert all("token_count" in c and "parent_id" in c for c in result["chunks"])
        client.post("/api/logout")
        assert client.get(path).status_code == 401


def test_text_highlight_stops_at_chunk_boundary_and_tracks_repeated_text():
    from types import SimpleNamespace

    class Splitter:
        tokenizer = SimpleNamespace(encode=lambda text: SimpleNamespace(ids=list(text)))

        def split(self, text, reserved=0):
            return [{"content": "ABC", "start": 0, "end": 3},
                    {"content": "ABC", "start": 4, "end": 7}]

    raw = make_pdf(["Example\nABC ABC"], font="Helvetica")
    result = indexed(raw, Splitter())
    first, second = result["chunks"]
    assert first["location_status"] == second["location_status"] == "complete"
    assert len(first["regions"]) == len(second["regions"]) == 1
    a, b = first["regions"][0], second["regions"][0]
    assert a["x"] + a["width"] < b["x"]
    assert abs(a["width"] - b["width"]) < 0.0001


def test_table_chunks_highlight_shared_source_cells():
    stream = BytesIO()
    table = Table([["Region", "Limit"], ["City A", "500"], ["", "400"]],
                  colWidths=[90, 90])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black),
                               ("SPAN", (0, 1), (0, 2))]))
    SimpleDocTemplate(stream).build([table])
    result = indexed(stream.getvalue(), LocalEmbeddings(Settings().model_cache_dir))
    a, b = result["chunks"]
    assert a["warnings"] and b["warnings"] and (a["row"], b["row"]) == (2, 3)
    assert a["location_status"] == b["location_status"] == "complete"
    assert len(a["regions"]) == len(b["regions"]) == 2
    assert a["regions"][0] == b["regions"][0]
    assert a["regions"][1]["y"] < b["regions"][1]["y"]
