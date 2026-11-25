"""逻辑表的文本：行明细（阅读）、行检索文本、表格信息卡与整表紧凑网格。"""

import re

UNIT_UNKNOWN = "未明确识别，请核对表头或注释"
BLANK = "空白（未推断）"
INHERITED = "※"
CARD_KEYS = 300  # 信息卡里行标识列取值的最多字数


def row_fields(table, row):
    return [{"name": name, **cell} for name, cell in zip(table["columns"], row["cells"])]


def plain_name(name):
    return re.sub(r"（列 \d+）$", "", name)


def common_path(names):
    parts = [plain_name(name).split(" / ") for name in names]
    shared = []
    for level in zip(*parts):
        if len(set(level)) > 1:
            break
        shared.append(level[0])
    return " / ".join(shared) or "、".join(dict.fromkeys(plain_name(name) for name in names))


def grouped_fields(table, row):
    """按原表语义合并字段：横向合并单元格只算一个字段，字段名取各列表头的共同上级；
    相邻同名列（如“地区（城市）”下的省、市两列）合成一个字段，各值依次连写。"""
    merged = []
    for name, cell in zip(table["columns"], row["cells"]):
        last = merged[-1] if merged else None
        if last and cell["bbox"] is not None and cell["bbox"] == last["bbox"]:
            last["names"].append(name)
        else:
            merged.append({"names": [name], "bbox": cell["bbox"], "value": cell["value"],
                           "inherited": "inherited_from" in cell})
    fields = []
    for item in merged:
        name = common_path(item["names"])
        if fields and fields[-1]["name"] == name:
            if item["value"] and item["value"] not in fields[-1]["values"]:
                fields[-1]["values"].append(item["value"])
            fields[-1]["inherited"] |= item["inherited"]
        else:
            fields.append({"name": name, "values": [item["value"]] if item["value"] else [],
                           "inherited": item["inherited"]})
    return [{"name": f["name"], "value": " ".join(f["values"]), "inherited": f["inherited"]}
            for f in fields]


def field_names(table):
    """信息卡与查表结果使用的列名，与 grouped_fields 的字段名一致。"""
    if not table["rows"]:
        return [plain_name(name) for name in dict.fromkeys(table["columns"])]
    return [field["name"] for field in grouped_fields(table, table["rows"][0])]


def row_key(table, row):
    """行标识：首个非序号字段的值。"""
    fields = grouped_fields(table, row)
    if table["serial"] and len(fields) > 1:
        fields = fields[1:]
    return fields[0]["value"]


def display_title(table, label):
    return table["title"] or table.get("lead") or label


def unit_suffix(table):
    return f"（单位：{table['unit']}）" if table["unit"] else ""


def row_content(table, row, title):
    """单行可独立阅读：带表名、单位和全部字段，空白明确标出。"""
    body = "；".join(f"{field['name']}：{field['value'] or BLANK}"
                    for field in grouped_fields(table, row))
    return f"表：{title}\n单位：{table['unit'] or UNIT_UNKNOWN}\n{body}"


def row_retrieval_text(table, row):
    """只保留非空值；多级表头连写为一个字段名，连续序号列不参与检索。"""
    parts = []
    for index, field in enumerate(grouped_fields(table, row)):
        if field["value"] and not (index == 0 and table["serial"]):
            parts.append(f"{field['name'].replace(' / ', '')}：{field['value']}")
    return "；".join(parts)


def table_card(table, title, path, keys=True):
    """表格信息卡：表名、所属位置、单位、列和各行标识，用于按主题命中整张表。

    keys=False 时省去行标识，用于搜索结果里已附上具体行的情况。"""
    lines = [f"表格：{title}"]
    if path:
        lines.append(f"所属：{path}")
    if table.get("lead") and table["lead"] != title:
        lines.append(f"说明：{table['lead']}")
    if table["unit"]:
        lines.append(f"单位：{table['unit']}")
    lines.append("列：" + "、".join(name.replace(" / ", "") for name in field_names(table)))
    names = "、".join(dict.fromkeys(row_key(table, row) for row in table["rows"] if row_key(table, row)))
    if keys and names:
        lines.append(f"行（共 {len(table['rows'])} 行）：" + (names if len(names) <= CARD_KEYS
                                                         else names[:CARD_KEYS] + "……"))
    if table.get("notes"):
        lines.append(table["notes"])
    return "\n".join(lines)


def whole_table_text(table, title, path):
    """小表整表检索文本：信息卡抬头加每行一条记录。"""
    head = [f"表格：{title}"] + ([f"所属：{path}"] if path else [])
    if table.get("lead") and table["lead"] != title:
        head.append(f"说明：{table['lead']}")
    if table["unit"]:
        head.append(f"单位：{table['unit']}")
    rows = [row_retrieval_text(table, row) for row in table["rows"]]
    return "\n".join(head + rows + ([table["notes"]] if table.get("notes") else []))


def grid_content(table, title):
    """整表文本：表头只写一次，每个数据行一行；返回文本及各行对应的来源页。"""
    inherited = any("inherited_from" in cell for row in table["rows"] for cell in row["cells"])
    note = "说明：每行一条数据，各值按“列”的顺序以 | 分隔；空白单元格未推断"
    if inherited:
        note += f"；{INHERITED} 表示承接上页合并单元格的值"
    head = [f"表：{title}", f"单位：{table['unit'] or UNIT_UNKNOWN}", note,
            "列：" + " | ".join(table["columns"])]
    if table.get("notes"):
        head.append(table["notes"])
    lines = [("\n".join(head), table["header_pages"][0])] + [
        (" | ".join(cell["value"] + (INHERITED if "inherited_from" in cell else "")
                    for cell in row["cells"]), row["page"])
        for row in table["rows"]]
    text, spans = "", []
    for line, page in lines:
        start = len(text)  # 换行符归入下一行，来源区间首尾相接
        text += ("\n" if text else "") + line
        spans.append({"start": start, "end": len(text), "page": page})
    return text, spans
