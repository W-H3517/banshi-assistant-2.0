from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Table, TableStyle
from sqlalchemy import select

from backend.app import create_app
from backend.config import Settings
from backend.db import Chunk, Document, DocumentParse
from backend.embeddings import LocalEmbeddings
from backend.indexing import prepare_document
from backend.knowledge import KnowledgeBase
from backend.ocr import LocalOCR
from backend.pdf_structure import assemble_document
from backend.reindex import apply, prepare
from tests_stage2.pdf_helpers import make_pdf
from tests_stage2.test_app import login


def test_toc_appendix_does_not_become_body_heading():
    lines = ["附录一：住宿表................67", "第一篇经费支出管理", "一、差旅费", "1.如何报销？", "凭据报销。"]
    blocks = [{"id": f"p1:b{i}", "page": 1, "kind": "text", "text": line,
               "edge": False, "method": "native", "confidence": None, "boxes": [None]*len(line)}
              for i, line in enumerate(lines)]
    result = assemble_document([{"number": 1, "blocks": blocks}], "测试")
    assert result["removed_lines"][0]["text"] == lines[0]
    assert [s["kind"] for s in result["sections"]] == ["heading", "heading", "article"]
    assert result["sections"][-1]["content"] == "1.如何报销？\n\n凭据报销。"


def test_mixed_pages_keep_tables_and_question_across_footer(tmp_path, monkeypatch):
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    output = BytesIO()
    canvas = Canvas(output, pagesize=(600, 800))
    canvas.setFont("STSong-Light", 12)
    canvas.drawString(60, 730, "示例财务手册")
    canvas.drawString(60, 680, "16.无住宿发票如何处理？")
    canvas.drawString(60, 640, "（1）举办方承担伙食时仅发首尾两天。")
    canvas.drawCentredString(300, 25, "- 1 -")
    canvas.showPage()
    canvas.setFont("STSong-Light", 12)
    canvas.drawString(60, 720, "（2）不承担市内交通费用的，按自然天数发放。")
    canvas.drawString(60, 670, "17.其他情形如何处理？")
    canvas.drawString(60, 630, "请提供相关证明。")
    canvas.drawCentredString(300, 25, "- 2 -")
    canvas.showPage()
    canvas.setFont("STSong-Light", 12)
    canvas.drawString(60, 730, "附录一：差旅住宿费标准明细表")
    canvas.drawString(60, 700, "单位：元/人/天")
    table = Table([["City", "Class III"], ["Dalian", "420"], ["Shanghai", "500"]])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    table.wrapOn(canvas, 400, 400)
    table.drawOn(canvas, 60, 500)
    canvas.showPage()
    canvas.drawImage(ImageReader(Image.new("RGB", (20, 20), "white")), 0, 0, 600, 800)
    canvas.save()
    calls = []

    def recognize(self, image):
        calls.append(1)
        return [{"text": "扫描页附注。", "confidence": .99, "polygon": [[100, 100], [900, 100],
                 [900, 180], [100, 180]], "words": []}]

    monkeypatch.setattr(LocalOCR, "recognize", recognize)
    _, snapshot = prepare_document(output.getvalue(), "mixed.pdf", LocalEmbeddings(Settings().model_cache_dir), LocalOCR(tmp_path))
    assert len(calls) == 1 and snapshot["preview"]["ocr_pages"] == [4]
    units = snapshot["structure"]["reading_units"]
    question = next(u for u in units if "16.无住宿" in u["content"])
    assert "不承担市内交通费用" in question["content"] and "17." not in question["content"]
    assert {s["page"] for s in question["spans"]} == {1, 2}
    assert all("- 1 -" not in c["content"] and "- 2 -" not in c["content"] for c in snapshot["preview"]["chunks"])
    row = next(u for u in units if u["kind"] == "table_row" and "Dalian" in u["content"])
    assert "420" in row["content"] and "Shanghai" not in row["content"]
    assert row["table"]["header_paths"] == ["City", "Class III"]
    assert row["table"]["unit"] == "元/人/天"
    assert row["table"]["unit_sources"][0]["page"] == 3
    assert "附录一" in row["content"]
    assert row["table"]["fields"][0]["bbox"] is not None
    assert all("content" not in n for n in snapshot["structure"]["nodes"])


def test_unknown_boundary_is_not_complete_and_raw_page_is_available(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        login(client)
        doc = client.post("/api/documents", files={"file": (
            "未完条款.pdf", make_pdf(["未完条款\n第一条 仅在下列条件下允许："]),
        )}).json()
        kb = client.app.state.db.knowledge
        located = client.portal.call(kb.locate, "第一条", doc["id"])
        target = located["items"][0]["node_id"]
        result = client.portal.call(kb.read, target)
        assert result["pagination_complete"] and not result["reading_complete"]
        assert result["boundary_status"] == "uncertain" and result["missing_or_uncertain_parts"]
        assert "complete" not in result
        raw = client.portal.call(kb.read, target, "page", 1, None, 1)
        assert raw["pagination_complete"] and not raw["reading_complete"]
        assert "仅在下列条件" in "".join(i["content"] for i in raw["items"])
        with pytest.raises(ValueError, match="页码"):
            client.portal.call(kb.read, target, "page", 1, None, 2)


def test_explicit_reindex_rolls_back_and_preserves_originals(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "app")
    with TestClient(create_app(settings)) as client:
        login(client)
        db = client.app.state.db
        conversation = client.post("/api/conversations").json()
        output = tmp_path / "prepared"

        async def state():
            async with db.sessions() as session:
                return (list((await session.execute(select(Document.id, Document.original).order_by(Document.id))).all()),
                        set(await session.scalars(select(Chunk.id))),
                        [p.payload["structure"]["generation"] for p in await session.scalars(
                            select(DocumentParse).order_by(DocumentParse.document_id))])

        before = client.portal.call(state)
        client.portal.call(prepare, db, settings, output)
        original_store = KnowledgeBase.store_index
        calls = []

        def failing_store(*args):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("simulated write failure after delete")
            return original_store(*args)

        monkeypatch.setattr(KnowledgeBase, "store_index", staticmethod(failing_store))
        with pytest.raises(RuntimeError, match="after delete"):
            client.portal.call(apply, db, output)
        assert client.portal.call(state) == before
        monkeypatch.setattr(KnowledgeBase, "store_index", staticmethod(original_store))
        client.portal.call(apply, db, output)
        after = client.portal.call(state)
        assert before[0] == after[0] and not before[1].intersection(after[1]) and before[2] != after[2]
        assert client.get(f"/api/conversations/{conversation['id']}").json() == conversation
