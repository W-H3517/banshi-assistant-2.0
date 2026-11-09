from copy import deepcopy
from io import BytesIO

from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

from backend.document_preview import convert_pdf
from backend.table_preview import describe_tables


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
