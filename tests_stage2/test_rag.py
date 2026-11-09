import asyncio
from io import BytesIO

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy import func, select

from backend.app import create_app
from backend.config import Settings
from backend.db import Chunk, Database, Document, User
from backend.embeddings import LocalEmbeddings
from backend.knowledge import MAX_RESULTS, DuplicateDocument, KnowledgeBase, terms
from backend.pdf import InvalidPDF, extract_pages
from tests_stage2.pdf_helpers import make_pdf
from tests_stage2.test_app import login


@pytest.fixture(scope="module")
def embeddings():
    # 使用实际本地中文模型，避免用假向量证明语义检索有效。
    return LocalEmbeddings(Settings().model_cache_dir)


def test_long_text_is_chunked_without_truncating_tail(embeddings):
    text = "长篇制度。" + "员工办理申请时应提交真实完整材料，并由负责人确认适用范围。" * 120
    text += "\n最终例外：应急采购金额上限为八千元。"
    context = "文件：长篇制度\n文件名：长篇制度.pdf\n章节：第一章\n条目：第一条\n正文：\n"
    chunks = embeddings.split(context, text)
    assert len(chunks) > MAX_RESULTS
    assert chunks[-1]["content"].endswith("应急采购金额上限为八千元。")
    assert all(chunk["content"] == text[chunk["start"]:chunk["end"]] for chunk in chunks)
    for chunk in chunks:
        chunk["retrieval_text"] = context + chunk["content"]
    assert all(len(chunk["retrieval_text"]) <= 1000 for chunk in chunks)
    assert all(len(embeddings.tokenizer.encode(chunk["retrieval_text"]).ids) <= 512 for chunk in chunks)
    vectors = embeddings.passages(chunks)
    assert vectors.shape == (len(chunks), 512)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)
    unusual = embeddings.split(context, "x" * 4000 + "最终条款")
    assert all(len(chunk["content"]) <= 1000 for chunk in unusual)
    assert unusual[-1]["content"].endswith("最终条款")


def test_detect_scanned_and_reject_encrypted_empty_and_broken_pdf():
    scanned = BytesIO()
    canvas = Canvas(scanned)
    canvas.drawImage(ImageReader(Image.new("RGB", (200, 200), "white")), 20, 20, 500, 700)
    canvas.showPage()
    canvas.save()
    scan_pages = []
    assert extract_pages(scanned.getvalue(), scan_pages=scan_pages) == [""]
    assert scan_pages == [1]
    with pytest.raises(InvalidPDF, match="OCR"):
        extract_pages(scanned.getvalue())
    with pytest.raises(InvalidPDF, match="加密"):
        extract_pages(make_pdf(["保密制度"], encrypt=StandardEncryption("password")))
    blank = BytesIO()
    canvas = Canvas(blank)
    canvas.showPage()
    canvas.save()
    with pytest.raises(InvalidPDF, match="没有可提取"):
        extract_pages(blank.getvalue())
    with pytest.raises(InvalidPDF):
        extract_pages(b"%PDF-1.7\nnot-a-real-pdf")


def test_pdf_upload_page_citation_auth_failure_and_restart(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path)
    raw = make_pdf([
        "会议室管理办法\n会议室预约由行政办公室处理。",
        "设备维修说明\n会议室投影仪发生故障时，拨打内部维修电话 8675309。",
    ])
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        result = client.post("/api/documents", files={"file": ("会议室.pdf", raw)})
        assert result.status_code == 201, result.text
        doc = result.json()
        assert doc["page_count"] == 2
        assert doc["chunk_count"] == 2
        path = f"/api/documents/{doc['id']}"
        original = client.get(path + "/file")
        assert original.content == raw
        assert original.headers["content-type"] == "application/pdf"
        assert original.headers["cache-control"] == "no-store"
        hits = client.portal.call(app.state.db.knowledge.search, "投影仪坏了找谁维修")
        hit = next(hit for hit in hits if "8675309" in hit["content"])
        assert hit["id"] == doc["id"] and hit["page"] == 2
        assert hit["citation"] == path + "#page=2"
        assert len(hits) <= MAX_RESULTS
        assert sum(len(hit["content"]) for hit in hits) <= 6000
        duplicate = client.post("/api/documents", files={"file": ("改名.pdf", raw)})
        assert duplicate.status_code == 409
        before = len(client.get("/api/documents").json())
        with monkeypatch.context() as patch:
            def fail(*args):
                raise RuntimeError("模拟嵌入故障")
            patch.setattr(app.state.db.knowledge.embeddings, "passages", fail)
            failure = client.post("/api/documents", files={
                "file": ("失败.pdf", make_pdf(["失败测试\n此文件不应入库。"] )),
            })
            assert failure.status_code == 503
            assert "未入库" in failure.json()["detail"]
        assert len(client.get("/api/documents").json()) == before
        client.post("/api/logout")
        assert client.get(path + "/file").status_code == 401
        login(client, "alice")
        assert client.get(path + "/file").content == raw
        assert client.post("/api/documents", files={"file": ("员工.pdf", raw)}).status_code == 403
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        assert client.get(path + "/file").content == raw
        hits = client.portal.call(app.state.db.knowledge.search, "内部维修电话")
        assert any(hit["id"] == doc["id"] and hit["page"] == 2 for hit in hits)
        assert len(client.get("/api/documents").json()) == 4


async def test_semantic_retrieval_and_duplicate_race(tmp_path, embeddings):
    settings = Settings(data_dir=tmp_path)
    db = Database(settings)
    await db.initialize()
    kb = KnowledgeBase(db, embeddings)
    try:
        await kb.seed()
        async with db.sessions() as session:
            admin = await session.scalar(select(User.id).where(User.username == "admin"))
        # 同义改写与相关片段没有分词交集，必须由真实向量检索命中。
        query = "酒店预算上限"
        hits = await kb.search(query)
        target = next(hit for hit in hits if "450" in hit["content"])
        assert not set(terms(query)).intersection(terms(target["title"] + target["content"]))
        assert any("450" in hit["content"] for hit in await kb.search("住宾馆最多花多少钱"))
        assert await kb.search("火星飞船燃料配比") == []
        assert await kb.search("生育假可以休几天") == []
        scoped = await kb.search("出差", target["id"], 1)
        assert all(hit["id"] == target["id"] and hit["page"] == 1 for hit in scoped)
        assert await kb.search("出差", target["id"], 99) == []
        with pytest.raises(ValueError):
            await kb.search("超长" * 301)
        raw = make_pdf(["并发制度\n打印机耗材需要登记。"])
        results = await asyncio.gather(
            kb.upload("并发.pdf", raw, admin), kb.upload("并发.pdf", raw, admin),
            return_exceptions=True,
        )
        assert sum(isinstance(result, dict) for result in results) == 1
        assert sum(isinstance(result, DuplicateDocument) for result in results) == 1
        async with db.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Document)) == 4
            assert await session.scalar(select(func.count()).select_from(Chunk)) == 4
        long_pdf = make_pdf([
            f"长文验收制度\n第 {number} 章\n" + "常规办理需先提交申请并说明用途。" * 60
            for number in range(1, 9)
        ] + ["长文验收制度\n最后一条：紧急采购的特别额度为 87654321 元，仅用于测试。"])
        long_doc = await kb.upload("长文.pdf", long_pdf, admin)
        last_hits = await kb.search("87654321 特别额度", long_doc["id"])
        assert last_hits and "87654321" in last_hits[0]["content"]
        assert last_hits[0]["page"] == long_doc["page_count"]
        assert len(last_hits) <= MAX_RESULTS
        assert sum(len(hit["content"]) for hit in last_hits) <= 6000
    finally:
        await db.engine.dispose()
