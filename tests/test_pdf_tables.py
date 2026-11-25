from copy import deepcopy
from io import BytesIO

from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from backend.indexing.table_text import grid_content, row_content, row_retrieval_text
from backend.parsing import parse_pdf
from backend.parsing.tables import build_tables, inherit_keys


def cell(value, box, row=1, column=1):
    return {"value": value, "bbox": box, "source_row": row, "source_column": column}


def fragment(number, page, values, *, top=0, boxes=None):
    """由文字矩阵生成一页表格片段；boxes 可覆盖个别单元格的来源框以表示合并。"""
    boxes = boxes or {}
    grid = [[cell(value, boxes.get((r, c), [c * 10, top + r * 10, c * 10 + 10, top + r * 10 + 10]),
                  r + 1, c + 1) for c, value in enumerate(row)] for r, row in enumerate(values)]
    return {"id": number, "kind": "table", "pages": [page], "content": "", "source_spans": [],
            "table_data": {"grid": grid,
                           "column_positions": [c * 10 for c in range(len(values[0]) + 1)]}}


def test_merged_cells_expand_only_with_actual_geometry():
    stream = BytesIO()
    table = Table([["Region", "Category", "Limit"], ["City A", "A", "500"],
                   ["", "B", "400"], ["", "C", "300"]], colWidths=[90, 90, 90])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black),
                               ("SPAN", (0, 1), (0, 2))]))
    SimpleDocTemplate(stream).build([table])
    parsed = parse_pdf(stream.getvalue())
    table, = parsed["tables"]
    rows = table["rows"]
    assert table["columns"] == ["Region", "Category", "Limit"]
    assert rows[0]["cells"][0]["value"] == "City A"
    assert rows[1]["cells"][0]["value"] == "City A"
    assert rows[1]["cells"][0]["source_row"] == 2
    assert rows[2]["cells"][0]["value"] == ""
    assert "空白（未推断）" in row_content(table, rows[2], "Example")

    # 相邻续页并入同一张逻辑表并沿用表头；上页本身空白的列不向续页填充。
    section = next(s for s in parsed["sections"] if s["kind"] == "table")
    continuation = deepcopy(section)
    continuation["id"] = 2
    continuation["pages"] = [2]
    continuation["table_data"]["grid"] = [continuation["table_data"]["grid"][-1]]
    table, = build_tables([section, continuation])
    assert table["header_pages"] == [1] and [f["page"] for f in table["fragments"]] == [1, 2]
    last = table["rows"][-1]
    assert last["page"] == 2 and last["continued"] and last["row"] == 5
    assert [c["value"] for c in last["cells"]] == ["", "C", "300"]


def test_header_only_table_keeps_headers_without_inheriting_them_as_data():
    header = fragment(1, 1, [["序号", "地区", "金额"]])
    continuation = fragment(2, 2, [["", "", "300"]])
    table, = build_tables([header, continuation])
    assert table["header_pages"] == [1] and len(table["rows"]) == 1
    assert [c["value"] for c in table["rows"][0]["cells"]] == ["", "", "300"]


def test_multi_level_header_follows_merged_cells_and_repeated_header_is_skipped():
    # “标准”横跨两列，其下还有一行子表头；“类别”纵向占满两行表头。数据行没有纯数字。
    boxes = {(0, 0): [0, 0, 10, 20], (1, 0): [0, 0, 10, 20],
             (0, 1): [10, 0, 30, 10], (0, 2): [10, 0, 30, 10]}
    values = [["类别", "标准", "标准"], ["类别", "形式", "时长"], ["评审", "会议", "半天"]]
    first = fragment(1, 1, values, boxes=boxes)
    repeated = fragment(2, 2, values[:2] + [["咨询", "访谈", "一天"]], boxes=boxes)
    table, = build_tables([first, repeated])
    assert table["columns"] == ["类别", "标准 / 形式", "标准 / 时长"]
    assert table["header_pages"] == [1, 2]
    assert [[c["value"] for c in row["cells"]] for row in table["rows"]] == [
        ["评审", "会议", "半天"], ["咨询", "访谈", "一天"]]
    # 中间隔着正文、不在相邻页或列边界不同，都是新表。
    text = {"id": 9, "kind": "text", "content": "另见下表：", "source_spans": []}
    assert len(build_tables([first, text, repeated])) == 2
    assert len(build_tables([first, fragment(2, 3, values, boxes=boxes)])) == 2
    assert len(build_tables([first, fragment(2, 2, [["甲", "乙"], ["丙", "丁"]])])) == 2


def test_cross_page_row_keys_continue_without_filling_amounts():
    first = fragment(1, 1, [["序号", "地区", "城市", "一类"], ["1", "重庆", "全市", "800"],
                            ["2", "四川", "成都", "900"]], top=70)
    # 续页页首：序号与地区是被分页截断的合并单元格，显示为跨两行的空白。
    merged = {(0, 0): [0, 0, 10, 20], (1, 0): [0, 0, 10, 20],
              (0, 1): [10, 0, 20, 20], (1, 1): [10, 0, 20, 20]}
    second = fragment(2, 2, [["", "", "阿坝", ""], ["", "", "其他地区", "300"],
                             ["3", "贵州", "贵阳", "500"]], boxes=merged)
    table, = build_tables([first, second])
    rows = table["rows"]
    assert [c["value"] for c in rows[2]["cells"]] == ["2", "四川", "阿坝", ""]
    assert [c["value"] for c in rows[3]["cells"]] == ["2", "四川", "其他地区", "300"]
    inherited = rows[2]["cells"][1]
    assert inherited["raw_value"] == "" and inherited["inherited_from"]["page"] == 1
    assert inherited["inherited_from"]["value"] == "四川"
    assert "inherited_from" not in rows[4]["cells"][1]
    # 整表文本标出继承值，行检索文本去掉连续序号与空白。
    text, spans = grid_content(table, "住宿标准")
    assert "2※ | 四川※ | 阿坝 | " in text and text.count("列：") == 1
    assert [s["page"] for s in spans] == [1, 1, 1, 2, 2, 2]
    assert spans[0]["start"] == 0 and spans[-1]["end"] == len(text)
    assert table["serial"] and row_retrieval_text(table, rows[2]) == "地区：四川；城市：阿坝"

    # 再往后一页连续继承时，来源仍指向最初有值的单元格。
    third = fragment(3, 3, [["", "", "遵义", "400"]])
    table, = build_tables([first, fragment(2, 2, [["", "", "阿坝", ""]]), third])
    assert table["rows"][-1]["cells"][1]["inherited_from"]["page"] == 1


def test_only_leading_row_keys_are_continued():
    def table_with(rows):
        return build_tables([fragment(1, 1, [["序号", "部门", "金额"], *rows], top=70)])[0]

    def top_row():
        return [[cell(v, [c * 10, 0, c * 10 + 10, 10]) for c, v in enumerate(["", "", ""])]]

    # 整行空白：行标识延续，数值列即使此前每行都有值也不填。
    table = table_with([["1", "财务处", "900"], ["2", "人事处", "800"]])
    body = top_row()
    inherit_keys(table, body, table["fragments"][0]["bottom"])
    assert [c["value"] for c in body[0]] == ["2", "人事处", ""]
    # 来源不在上页页尾时不继承。
    body = top_row()
    inherit_keys(table, body, 200)
    assert [c["value"] for c in body[0]] == ["", "", ""]
    # 此前本就出现过空白的列不是必填的行标识。
    table = table_with([["1", "", "900"], ["2", "人事处", "800"]])
    body = top_row()
    inherit_keys(table, body, table["fragments"][0]["bottom"])
    assert [c["value"] for c in body[0]] == ["2", "", ""]
    # 行首已有值时，其后的空白一律保留。
    body = top_row()
    body[0][0]["value"] = "3"
    inherit_keys(table, body, table["fragments"][0]["bottom"])
    assert [c["value"] for c in body[0]] == ["3", "", ""]


def test_row_retrieval_text_joins_header_path_and_omits_blanks():
    values = [["年份", "地区", "地区", "住宿费标准", "旺季浮动标准", "备注"],
              ["年份", "地区", "地区", "三类", "期间", "备注"],
              ["2025", "大连", "全市", "350", "7-9月", ""]]
    boxes = {(r, c): [c * 10, 0, c * 10 + 10, 20] for r in (0, 1) for c in (0, 5)}
    boxes |= {(r, c): [10, 0, 30, 20] for r in (0, 1) for c in (1, 2)}
    table, = build_tables([fragment(1, 1, values, boxes=boxes)])
    assert table["columns"] == ["年份", "地区（列 2）", "地区（列 3）", "住宿费标准 / 三类",
                                "旺季浮动标准 / 期间", "备注"]
    text = row_retrieval_text(table, table["rows"][0])
    # 年份不是 1、2、3… 的序号，照常保留；同名的相邻列（省、市）合成一个字段，单位由片段前缀给出。
    assert text == "年份：2025；地区：大连 全市；住宿费标准三类：350；旺季浮动标准期间：7-9月"
