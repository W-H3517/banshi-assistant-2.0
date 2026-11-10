from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy import func, select

from backend.app import create_app
from backend.config import Settings
from backend.db import Chunk, Document, DocumentParse
from backend.embeddings import LocalEmbeddings
from backend.indexing import INDEX_VERSION, assign_context, build_chunks
from backend.ocr import LocalOCR, OCRUnavailable, model_paths
from backend.pdf import InvalidPDF, extract_pages
from backend.pdf_structure import convert_pdf
from tests_stage2.test_app import login


def scanned_pdf(*, mixed=False):
    output = BytesIO()
    canvas = Canvas(output, pagesize=(600, 800))
    for _ in range(2):
        canvas.drawImage(ImageReader(Image.new("RGB", (20, 20), "white")), 0, 0, 600, 800)
        canvas.showPage()
    if mixed:
        canvas.setFont("Helvetica", 12)
        canvas.drawString(60, 700, "Native text page remains selectable and is not sent to OCR.")
        canvas.showPage()
    canvas.save()
    return output.getvalue()


def fake_recognizer(monkeypatch):
    calls = []

    def recognize(self, image):
        number = len(calls) % 2
        calls.append(image.size)
        rows = ([('通知', .2, .1, .7), ('1.办理事项。申请人应当', .16, .8, .9),
                 ('提交完整材料并在审核', .1, .85, .9)] if number == 0 else
                [('通过后办理。不得提前领取。', .1, .1, .7),
                 ('2.材料保留三年。', .16, .2, .7)])
        lines = []
        for text, left, top, right in rows:
            def polygon(a, b, top=top):
                return [[a*image.width, top*image.height], [b*image.width, top*image.height],
                        [b*image.width, (top+.04)*image.height],
                        [a*image.width, (top+.04)*image.height]]
            step = (right-left)/len(text)
            lines.append({'text': text, 'confidence': .99, 'polygon': polygon(left, right),
                          'words': [[ch, .99, polygon(left+i*step, left+(i+1)*step)]
                                    for i, ch in enumerate(text)]})
        return lines

    monkeypatch.setattr(LocalOCR, "recognize", recognize)
    return calls


def test_paragraph_mapping_mixed_pages_and_long_paragraph_budget(tmp_path, monkeypatch):
    calls = fake_recognizer(monkeypatch)
    raw = scanned_pdf(mixed=True)
    scanned = []
    pages = extract_pages(raw, scan_pages=scanned)
    assert scanned == [1, 2] and "Native text" in pages[2]
    parsed = convert_pdf(raw, "通知", ocr=LocalOCR(tmp_path), scan_pages=scanned)
    assert len(calls) == 2
    embeddings = LocalEmbeddings(Settings().model_cache_dir)
    assign_context(parsed["sections"], "通知")
    preview = build_chunks(parsed, "通知", "通知.pdf", embeddings)
    cross_page = next(c for c in preview['chunks'] if len(c['pages']) > 1)
    assert cross_page['content'].replace('\n', '') == '1.办理事项。申请人应当提交完整材料并在审核通过后办理。不得提前领取。'
    assert cross_page['pages'] == [1, 2] and len(cross_page['regions']) == 3
    assert any('Native text' in c['content'] for c in preview['chunks'])
    for chunk in preview['chunks']:
        assert chunk['location_status'] == 'complete'
        for rect in chunk['regions']:
            assert 0 <= rect['x'] < rect['x']+rect['width'] <= 1
            assert 0 <= rect['y'] < rect['y']+rect['height'] <= 1
    # 放大同段文本，确认不会以截断方式丢掉尾部，也不超过向量输入上限。
    section = parsed['sections'][1]
    section['content'] = '申请人必须提供完整凭证并核对金额。' * 100 + '最终例外须保留。'
    section['source_spans'] = [{'start': 0, 'end': len(section['content']), 'page': 1,
                               'boxes': [[60, 100, 90, 120]] * len(section['content']),
                               'confidence': .99}]
    parsed['sections'] = [section]
    chunks = build_chunks(parsed, '通知', '通知.pdf', embeddings)['chunks']
    assert len(chunks) > 1 and chunks[-1]['content'].endswith('最终例外须保留。')
    assert all(c['token_count'] <= 512 for c in chunks)
    assert all('超过输入预算' in ''.join(c['warnings']) for c in chunks)
    embeddings.passages(chunks)


def test_ocr_models_missing_never_download_during_request(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        pytest.fail('request must not download models')
    monkeypatch.setattr('backend.ocr.urlopen', no_network)
    with pytest.raises(OCRUnavailable, match='backend.prepare'):
        model_paths(tmp_path)


def test_scanned_upload_preview_search_restart_and_delete(tmp_path, monkeypatch):
    calls = fake_recognizer(monkeypatch)
    settings = Settings(data_dir=tmp_path)
    raw = scanned_pdf()
    with TestClient(create_app(settings)) as client:
        login(client)
        response = client.post('/api/documents', files={'file': ('扫描通知.pdf', raw)})
        assert response.status_code == 201, response.text
        document = response.json()
        path = f"/api/documents/{document['id']}"
        assert len(calls) == 2 and document['chunk_count'] == 2
        preview = client.get(path+'/chunk-preview').json()
        assert preview['version'] == INDEX_VERSION and preview['ocr_pages'] == [1, 2]
        assert client.get(path+'/file').content == raw
        assert client.get(path+'/chunk-preview').json() == preview and len(calls) == 2
        hits = client.portal.call(client.app.state.db.knowledge.search, '完整材料', document['id'])
        hit = next(h for h in hits if '不得提前领取' in h['content'])
        assert hit['pages'] == [1, 2] and hit['page'] == 1 and hit['citation'].endswith('#page=1')
        assert hit['warnings']
        async def check_rows():
            async with client.app.state.db.sessions() as session:
                chunks = list(await session.scalars(select(Chunk).where(
                    Chunk.document_id == document['id']).order_by(Chunk.ordinal)))
                assert [c.content for c in chunks] == [c['retrieval_text'] for c in preview['chunks']]
        client.portal.call(check_rows)
        assert client.post('/api/documents', files={'file': ('重复.pdf', raw)}).status_code == 409
        login(client, 'alice')
        assert client.get(path+'/chunk-preview').status_code == 403
        client.post('/api/logout')
        assert client.get(path+'/chunk-preview').status_code == 401
    with TestClient(create_app(settings)) as client:
        login(client)
        assert client.get(path+'/chunk-preview').json() == preview and len(calls) == 2
        assert client.delete(path).status_code == 200
        async def check_deleted():
            async with client.app.state.db.sessions() as session:
                assert await session.get(DocumentParse, document['id']) is None
        client.portal.call(check_deleted)


def test_ocr_failure_and_embedding_failure_leave_no_partial_data(tmp_path, monkeypatch):
    raw = scanned_pdf()
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        login(client)
        before = client.get('/api/documents').json()
        monkeypatch.setattr(LocalOCR, 'recognize', lambda *args: [])
        response = client.post('/api/documents', files={'file': ('空白扫描.pdf', raw)})
        assert response.status_code == 422 and '未识别到文字' in response.text
        fake_recognizer(monkeypatch)
        def fail(*args):
            raise RuntimeError('embedding failure')
        monkeypatch.setattr(client.app.state.db.knowledge.embeddings, 'passages', fail)
        assert client.post('/api/documents', files={'file': ('失败.pdf', raw)}).status_code == 503
        assert client.get('/api/documents').json() == before
        async def counts():
            async with client.app.state.db.sessions() as session:
                assert await session.scalar(select(func.count()).select_from(DocumentParse)) == len(before)
                assert await session.scalar(select(func.count()).select_from(Document)) == len(before)
        client.portal.call(counts)


def test_empty_scanned_page_is_not_silently_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(LocalOCR, 'recognize', lambda *args: [])
    with pytest.raises(InvalidPDF, match='第 1 页未识别到文字'):
        convert_pdf(scanned_pdf(), ocr=LocalOCR(tmp_path), scan_pages=[1, 2])
