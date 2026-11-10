from copy import deepcopy
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
from backend.pdf_tables import continue_region_keys, retrieval_row
from tests_stage2.pdf_helpers import make_pdf
from tests_stage2.test_app import login


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
        snap, _ = client.portal.call(kb.snapshots, doc['id'])[doc['id']]
        assert not any('特此通知' in c['content'] for c in snap['preview']['chunks'])
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
    assert all(1 not in chunk['pages'] for chunk in snapshot['preview']['chunks'])


def test_cross_page_keys_have_geometry_and_do_not_fill_amounts():
    def cell(value, box):
        return {'value': value, 'bbox': box, 'source_row': 1, 'source_column': 1}
    origin = [cell('28', [0, 90, 10, 100]), cell('四川', [10, 90, 20, 100]),
              cell('成都', [20, 90, 30, 100]), cell('900', [30, 90, 40, 100])]
    grid = [[cell('', [0, 0, 10, 20]), cell('', [10, 0, 20, 20]),
             cell('阿坝', [20, 0, 30, 10]), cell('', [30, 0, 40, 10])],
            [cell('', [0, 0, 10, 20]), cell('', [10, 0, 20, 20]),
             cell('其他地区', [20, 10, 30, 20]), cell('300', [30, 10, 40, 20])]]
    previous = {'last_fields': origin, 'page': 1, 'bottom': 100}
    result = continue_region_keys(grid, ['序号', '地区', '地区', '一类'], previous)
    assert all(row[1]['value'] == '四川' for row in result)
    assert result[0][1]['inherited_from']['page'] == 1 and result[0][1]['raw_value'] == ''
    assert result[0][3]['value'] == '' and grid[0][1]['value'] == ''
    # 非页尾来源、不同列语义不能继承。
    wrong = deepcopy(previous)
    wrong['bottom'] = 200
    assert continue_region_keys(grid, ['序号', '地区', '地区', '一类'], wrong) == grid
    assert continue_region_keys(grid, ['序号', '部门', '金额', '一类'], previous) == grid

    # 只有一个地区列时，第三列可能已是金额，不能按位置继承。
    short_grid = [[cell('', [0, 0, 10, 10]), cell('', [10, 0, 20, 10]),
                   cell('', [20, 0, 30, 10])]]
    short_previous = {'last_fields': origin[:2] + [cell('900', [20, 90, 30, 100])],
                      'page': 1, 'bottom': 100}
    short_result = continue_region_keys(short_grid, ['序号', '地区', '金额'], short_previous)
    assert short_result[0][1]['value'] == '四川' and short_result[0][2]['value'] == ''


def test_table_summary_preserves_season_and_omits_empty_fields():
    fields = [{'name': name, 'value': value} for name, value in [
        ('序号', '7'), ('地区（列2）', '大连'), ('地区（列3）', '全市'),
        ('住宿费标准 / 三类', '350'), ('旺季浮动标准 / 旺季 / 期间', '7-9月'),
        ('旺季浮动标准 / 旺季上浮价 / 三类', '420'), ('备注', ''),
    ]]
    text = retrieval_row(fields, '元/人·天', '住宿表')
    assert all(term in text for term in ['大连', '全市', '普通三类：350', '旺季三类：420', '7-9月', '元/人·天'])
    assert '空白' not in text and '备注' not in text and '序号' not in text


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
        for invalid in [[], ['单个'], ['重复', '重复'], ['a']*5, [' ', 'b']]:
            with pytest.raises(ValueError):
                client.portal.call(kb.search_bundle, invalid)
