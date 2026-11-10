from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import create_app
from backend.config import Settings
from backend.db import DocumentParse
from backend.embeddings import LocalEmbeddings
from backend.indexing import assign_context, build_chunks, prepare_document
from backend.pdf_structure import assemble_document
from tests_stage2.pdf_helpers import make_pdf
from tests_stage2.test_app import login


def test_native_context_is_embedded_and_titles_are_not_chunks():
    embeddings = LocalEmbeddings(Settings().model_cache_dir)
    raw = make_pdf([
        '报销办法\n第一章 总则\n第一条 申请人应提交完整凭证，',
        '经审核通过后办理。\n第二章 特殊规定\n第二条 不得重复报销。',
    ])
    _, snapshot = prepare_document(raw, '费用制度.pdf', embeddings, None)
    chunks = snapshot['preview']['chunks']
    assert len(chunks) == 2
    assert chunks[0]['pages'] == [1, 2]
    assert chunks[0]['source'] == {'document_title': '报销办法', 'filename': '费用制度.pdf',
                                  'chapter': '第一章总则', 'article': '第一条'}
    assert chunks[1]['source']['chapter'] == '第二章特殊规定'
    assert chunks[1]['source']['article'] == '第二条'
    for chunk in chunks:
        assert chunk['content'].startswith(chunk['source']['article'])
        assert chunk['retrieval_text'] == chunk['content']
        assert '文件：' not in chunk['retrieval_text']
        assert chunk['token_count'] == len(embeddings.tokenizer.encode(chunk['retrieval_text']).ids)
        assert chunk['location_status'] == 'complete'


def test_ocr_heading_and_article_survive_page_break_without_extra_title_chunks():
    def line(text, x, y, right=550):
        return {'text': text, 'polygon': [[x,y],[right,y],[right,y+20],[x,y+20]],
                'boxes': [[x,y,right,y+20]]*len(text), 'confidence': .99}
    pages = [
        {'number': 1, 'width': 600, 'height': 800, 'view_box': [0,0,600,800], 'ocr': True,
         'lines': [line('出差通知',150,100), line('一、规范出行',100,150),
                   line('1.等级标准。公务出差应优先',80,650), line('选择经济便捷的交通工具并',50,680)]},
        {'number': 2, 'width': 600, 'height': 800, 'view_box': [0,0,600,800], 'ocr': True,
         'lines': [line('按规定等级乘坐。',50,100), line('二、报销手续',100,150),
                   line('1.凭证要求。应当提交完整凭证。',80,200), line('特此通知。',50,250)]},
    ]
    for page in pages:
        page['blocks'] = [{'id': f"p{page['number']}:b{i}", 'page': page['number'], 'kind': 'text',
                              'text': line['text'], 'boxes': line['boxes'], 'confidence': line['confidence'],
                              'method': 'ocr', 'bbox': [line['polygon'][0][0], line['polygon'][0][1],
                                                  line['polygon'][2][0], line['polygon'][2][1]], 'edge': False}
                          for i,line in enumerate(page.pop('lines'))]
    parsed = assemble_document(pages, '出差通知')
    assign_context(parsed['sections'], '出差通知')
    chunks = build_chunks(parsed, '出差通知', '通知原件.pdf', LocalEmbeddings(Settings().model_cache_dir))['chunks']
    assert chunks[0]['source']['chapter'] == '一、规范出行'
    assert chunks[0]['source']['article'] == '1.等级标准'
    assert chunks[0]['pages'] == [1,2] and len(chunks[0]['regions']) == 3
    assert chunks[1]['source']['chapter'] == '二、报销手续'
    assert chunks[1]['source']['article'] == '1.凭证要求'
    assert all(c['content'] not in ('出差通知', '一、规范出行', '二、报销手续') for c in chunks)


def test_nested_headings_keep_article_and_do_not_leak_to_next_article():
    texts = ['办法', '第一章总则', '第一条', '（一）申请材料', '1.凭证。应当完整。',
             '第二条 应当留档。', '第二章费用', '1.标准。费用不得超过限额。']
    sections = [{'content': text, 'kind': 'text', 'pages': [1]} for text in texts]
    assign_context(sections, '办法')
    assert sections[4]['article'] == '第一条 / 1.凭证'
    assert sections[4]['chapter'] == '第一章总则 / （一）申请材料'
    assert sections[5]['article'] == '第二条' and sections[5]['chapter'] == '第一章总则'
    assert sections[7]['article'] == '1.标准' and sections[7]['chapter'] == '第二章费用'


def test_old_index_is_rejected_without_migration(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        login(client)
        kb = client.app.state.db.knowledge
        async def make_obsolete():
            async with kb.db.sessions.begin() as session:
                for snapshot in await session.scalars(select(DocumentParse)):
                    payload = deepcopy(snapshot.payload)
                    payload['version'] = 'discarded-test-index'
                    snapshot.payload = payload
        client.portal.call(make_obsolete)
        with pytest.raises(ValueError, match='旧索引'):
            client.portal.call(kb.check_index)
        async def inspect():
            async with kb.db.sessions() as session:
                return [p.payload['version'] for p in await session.scalars(select(DocumentParse))]
        assert set(client.portal.call(inspect)) == {'discarded-test-index'}
