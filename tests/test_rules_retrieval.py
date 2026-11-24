from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas

from backend.app import create_app
from backend.config import Settings
from backend.embeddings import LocalEmbeddings
from backend.indexing import prepare_document
from tests.pdf_helpers import make_pdf
from tests.test_app import login


def test_effect_separated_signature_excluded_and_questions_locatable(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        login(client)
        doc = client.post('/api/documents', files={'file': ('通知.pdf', make_pdf([
            '审核通知\n1.材料如何提交？\n完整材料交部门。\n本通知自发布之日起施行。此前规定与本通知不一致的，以本通知为准。\n特此通知。\n财务处\n2026年1月30日',
        ]))}).json()
        kb = client.app.state.db.knowledge
        located = client.portal.call(kb.locate, '材料如何提交', doc['id'])
        assert located['total'] == 1 and located['items'][0]['label'] == '1.材料如何提交？'
        body = client.portal.call(kb.read, located['items'][0]['node_id'])
        assert '本通知' not in ''.join(i['content'] for i in body['items'])
        bundle = client.portal.call(kb.search_bundle, ['材料提交', '审核规定'], doc['id'])
        assert len(bundle['rule_cards']) == 1
        rule = bundle['rule_cards'][0]['rules'][0]
        assert rule['content'].startswith('本通知') and rule['boundary_status'] == 'confirmed'
        assert '财务处' not in rule['content']
        chunks = client.portal.call(kb.preview, doc['id'], True)['chunks']
        assert not any('特此通知' in c['content'] for c in chunks)
        raw = client.portal.call(kb.read, rule['node_id'], 'page', 1, None, 1)
        assert any('财务处' in i['content'] for i in raw['items'])


def test_cover_title_and_revision_are_not_search_chunks():
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
    output = BytesIO()
    c = Canvas(output)
    c.setFont('STSong-Light', 12)
    c.drawString(60, 750, '机构英文名称')
    c.setFont('STSong-Light', 30)
    c.drawString(60, 650, '示例财务手册')
    c.setFont('STSong-Light', 12)
    c.drawString(300, 150, '计划财务处')
    c.drawString(300, 120, '2026年1月')
    c.showPage()
    c.setFont('STSong-Light', 12)
    c.drawString(60, 750, '1.如何处理？')
    c.drawString(60, 720, '按规定办理。')
    c.save()
    embeddings = LocalEmbeddings(Settings().model_cache_dir)
    title, snapshot = prepare_document(output.getvalue(), 'handbook.pdf', embeddings, None)
    assert title == '示例财务手册'
    assert all(1 not in chunk['pages'] for chunk in snapshot['chunks'])


def test_multiple_queries_reserve_candidates_and_rules_have_references(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        login(client)
        kb = client.app.state.db.knowledge
        texts = ['费用标准\n第一条 伙食每天补助100元。\n第二条 市内交通每天80元。\n第三条 因公临时出国（境）人员执行《境外经费办法》。',
                 '采购标准\n第一条 打印机采购须登记设备编号。']
        for i, text in enumerate(texts):
            client.post('/api/documents', files={'file': (f'{i}.pdf', make_pdf([text]))})
        result = client.portal.call(kb.search_bundle, ['伙食每天标准', '打印机采购登记'])
        assert all(r['returned'] > 0 for r in result['query_results'])
        assert len({i['node_id'] for i in result['items']}) == len(result['items'])
        assert len({c['id'] for c in result['rule_cards']}) == len(result['rule_cards'])
        refs = [ref for c in result['rule_cards'] for r in c['rules'] for ref in r['references']]
        assert any(r['title'] == '境外经费办法' and r['status'] == 'not_matched' for r in refs)
        for invalid in ['单个', [], ['重复', '重复'], ['住宿', '订票', '补助', '审批', '舱位'], [' ', 'b']]:
            with pytest.raises(ValueError):
                client.portal.call(kb.search_bundle, invalid)
