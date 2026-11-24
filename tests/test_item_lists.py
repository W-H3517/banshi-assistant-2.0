from backend.config import Settings
from backend.embeddings import LocalEmbeddings
from backend.indexing.chunks import build_chunks
from backend.indexing.structure import build_structure
from backend.parsing.ocr import char_boxes
from backend.parsing.sections import assemble_document


def ocr_pages(lines):
    """单页 OCR 行：x=80 为首行缩进，x=50 为折行续行。"""
    page = {'number': 1, 'width': 600, 'height': 2000, 'view_box': [0, 0, 600, 2000], 'ocr': True}
    page['blocks'] = [{'id': f'p1:b{i}', 'page': 1, 'kind': 'text', 'text': text,
                       'boxes': [[x, 300 + i * 30, 550, 320 + i * 30]] * len(text),
                       'confidence': .99, 'method': 'ocr', 'edge': False,
                       'bbox': [x, 300 + i * 30, 550, 320 + i * 30]}
                      for i, (x, text) in enumerate(lines)]
    return [page]


def wrap(text, width=30):
    """首行缩进（x=80），其余按宽度折行贴左边界（x=50）。"""
    return [(80 if i == 0 else 50, text[i:i + width]) for i in range(0, len(text), width)]


def build(lines):
    pages = ocr_pages(lines)
    parsed = assemble_document(pages, '')
    structure = build_structure(parsed['sections'], [], '采购办法', '采购办法.pdf')
    return structure, build_chunks(structure, [], pages, '采购办法',
                                   LocalEmbeddings(Settings().model_cache_dir))


def test_short_list_stays_one_chunk_with_cap():
    _, chunks = build([
        (80, '第十七条计划财务部门的主要职责包括：'),
        (80, '(一）负责政府采购预算的上报。'),
        (80, '(二）负责对采购预算进行审核。'),
        (80, '第十八条纪律检查部门负责监督。'),
    ])
    assert [c['content'] for c in chunks] == [
        '第十七条计划财务部门的主要职责包括：\n\n(一）负责政府采购预算的上报。\n\n(二）负责对采购预算进行审核。',
        '第十八条纪律检查部门负责监督。']


def test_long_list_splits_per_item_and_repeats_cap_without_highlighting_it():
    filler = '负责采购合同的归口管理包括法务审查流程审批以及合同范本制定' * 4
    structure, chunks = build([
        (80, '第十五条办公室的主要职责包括：'),
        *wrap('(一）' + filler + '。'),      # 首行折行、无标点，不能当小标题
        *wrap('(二）' + filler + '。'),
        *wrap('(三）' + filler + '。'),
        (80, '本条所列职责由办公室统一负责。'),  # 分点后的缩进段落不并入最后一项
    ])
    cap = '第十五条办公室的主要职责包括：\n'
    items = [c for c in chunks if c['content'].startswith(cap)]
    assert [c['content'][len(cap):len(cap) + 3] for c in items] == ['(一）', '(二）', '(三）']
    assert items[0]['content'] == cap + '(一）' + filler + '。'
    assert all(c['source']['article'] == '第十五条' and c['source']['chapter'] == '' for c in items)
    # 高亮只覆盖分点自身的行，帽句行（y=300）不在其中。
    assert all(r['y'] > 300 / 2000 for c in items for r in c['regions'])
    assert chunks[-1]['content'] == '本条所列职责由办公室统一负责。'
    assert not any(c['content'] == '第十五条办公室的主要职责包括：' for c in chunks)
    # 阅读单元里帽句只存一份。
    assert sum(u['content'].startswith('第十五条') for u in structure['units']) == 1


def test_item_subtitle_and_indented_body_stay_in_item():
    body = '谈判小组由采购人代表和评审专家组成原则上应为三人以上单数其中评审专家不少于成员总数的三分之二'
    _, chunks = build([
        (80, '第六十二条 竞争性谈判'),
        (80, '(一）成立谈判小组'),
        *wrap(body * 4 + '。'),
        (80, '(二）发出谈判文件'),
        *wrap(body * 4 + '。'),
    ])
    assert chunks[0]['content'] == '第六十二条 竞争性谈判\n(一）成立谈判小组\n' + body * 4 + '。'
    assert chunks[1]['content'].startswith('第六十二条 竞争性谈判\n(二）发出谈判文件\n')


def test_colon_paragraph_before_heading_like_items_is_not_a_cap():
    sections = assemble_document(ocr_pages([
        (80, '各部门：现就有关事项通知如下：'),
        (80, '一、严控交通工具选择'),
        (80, '1.严格遵循等级标准。'),
    ]), '')['sections']
    assert not any(s['kind'] == 'item' for s in sections)


def test_ocr_char_boxes_skip_line_spaces():
    word = lambda text, x: (text, .9, [[x, 0], [x + 10, 0], [x + 10, 10], [x, 10]])
    boxes = char_boxes('（二） 询价单', [word('（二）', 0), word('询价单', 40)], 1, 1, (0, 0))
    assert boxes[3] is None and boxes[4] == [40, 0, 50, 10] and len(boxes) == 7
    assert char_boxes('询价单', [word('报价单', 0)], 1, 1, (0, 0)) == [None] * 3
