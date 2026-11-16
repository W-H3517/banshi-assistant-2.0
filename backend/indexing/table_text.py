"""逻辑表的三种文本：行明细（阅读）、行检索文本（索引）、整表紧凑网格（阅读）。"""

import re

UNIT_UNKNOWN = "未明确识别，请核对表头或注释"
BLANK = "空白（未推断）"
INHERITED = "※"


def row_fields(table, row):
    return [{"name": name, **cell} for name, cell in zip(table["columns"], row["cells"])]


def row_content(table, row, title):
    """单行可独立阅读：带表名、单位和全部字段，空白明确标出。"""
    body = "；".join(f"{name}：{cell['value'] or BLANK}"
                    for name, cell in zip(table["columns"], row["cells"]))
    return f"表：{title}\n单位：{table['unit'] or UNIT_UNKNOWN}\n{body}"


def row_retrieval_text(table, row):
    """只保留非空值；多级表头连写为一个字段名，连续序号列不参与检索。"""
    parts = []
    for index, (name, cell) in enumerate(zip(table["columns"], row["cells"])):
        if cell["value"] and not (index == 0 and table["serial"]):
            name = re.sub(r"（列 \d+）$", "", name).replace(" / ", "")
            parts.append(f"{name}：{cell['value']}")
    return "；".join(parts) + (f"（{table['unit']}）" if table["unit"] and parts else "")


def grid_content(table, title):
    """整表文本：表头只写一次，每个数据行一行；返回文本及各行对应的来源页。"""
    inherited = any("inherited_from" in cell for row in table["rows"] for cell in row["cells"])
    note = "说明：每行一条数据，各值按“列”的顺序以 | 分隔；空白单元格未推断"
    if inherited:
        note += f"；{INHERITED} 表示承接上页合并单元格的值"
    head = "\n".join([f"表：{title}", f"单位：{table['unit'] or UNIT_UNKNOWN}", note,
                      "列：" + " | ".join(table["columns"])])
    lines = [(head, table["header_pages"][0])] + [
        (" | ".join(cell["value"] + (INHERITED if "inherited_from" in cell else "")
                    for cell in row["cells"]), row["page"])
        for row in table["rows"]]
    text, spans = "", []
    for line, page in lines:
        start = len(text)  # 换行符归入下一行，来源区间首尾相接
        text += ("\n" if text else "") + line
        spans.append({"start": start, "end": len(text), "page": page})
    return text, spans
