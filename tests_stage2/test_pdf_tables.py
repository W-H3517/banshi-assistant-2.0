from copy import deepcopy
from io import BytesIO

from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from backend.pdf_structure import convert_pdf
from backend.pdf_tables import describe_tables


def test_merged_cells_expand_only_with_actual_geometry():
    stream = BytesIO()
    table = Table([["Region", "Category", "Limit"], ["City A", "A", "500"],
                   ["", "B", "400"], ["", "C", "300"]], colWidths=[90, 90, 90])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black),
                               ("SPAN", (0, 1), (0, 2))]))
    SimpleDocTemplate(stream).build([table])
    converted = convert_pdf(stream.getvalue())
    result = describe_tables(converted["sections"], "Example")
    rows = result[0]["rows"]
    assert rows[0]["fields"][0]["value"] == "City A"
    assert rows[1]["fields"][0]["value"] == "City A"
    assert rows[1]["fields"][0]["source_row"] == 2
    assert rows[2]["fields"][0]["value"] == ""
    assert "空白（未推断）" in rows[2]["content"]

    # 相邻续页只能候选继承表头，不继承上页的地区数据。
    section = converted["sections"][0]
    continuation = deepcopy(section)
    continuation["id"] = 2
    continuation["pages"] = [2]
    continuation["table_data"]["grid"] = [continuation["table_data"]["grid"][-1]]
    tables = describe_tables([section, continuation], "Example")
    assert tables[1]["header_pages"] == [1]
    assert tables[1]["rows"][0]["fields"][0]["value"] == ""
    assert tables[1]["needs_review"] is True


def test_header_only_table_keeps_headers_without_inheriting_them_as_data():
    def cell(value, column):
        return {"value": value, "bbox": [column * 30, 0, (column + 1) * 30, 20],
                "source_row": 1, "source_column": column + 1}

    header = {"id": 1, "kind": "table", "chapter": "住宿标准", "pages": [1],
              "table_data": {"grid": [[cell(v, i) for i, v in enumerate(['序号', '地区', '金额'])]],
                             "column_positions": [0, 30, 60, 90]}}
    continuation = deepcopy(header)
    continuation.update(id=2, pages=[2])
    continuation["table_data"]["grid"] = [[cell(v, i) for i, v in enumerate(['', '', '300'])]]
    tables = describe_tables([header, continuation], "住宿标准")
    assert tables[0]["rows"] == []
    assert tables[1]["header_pages"] == [1]
    assert [f["value"] for f in tables[1]["rows"][0]["fields"]] == ['', '', '300']
