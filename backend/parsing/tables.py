"""表格解析：提取物理网格，并把相邻页的片段合并为逻辑表。

逻辑表的表头、单位只保存一次，每行保留来源页与单元格坐标。只展开实际合并的单元格，
空白不向下填充；跨页时仅延续被分页截断的行标识列，并记录继承来源。
"""

import re
from itertools import pairwise

NUMERIC = re.compile(r"\d+(?:\.\d+)?")
UNIT = re.compile(r"单位\s*[:：]\s*([^\n]+)")
TOLERANCE = 2  # PDF 点：判断列边界一致和页首、页尾对齐
CAPTION_LIMIT = 250


def cell_text(value):
    return re.sub(r"(?<=[一-鿿])\s+(?=[一-鿿])", "",
                  (value or "").replace("\n", " ")).strip()


def extract_grid(table):
    """按全部单元格边界切成规则网格；合并单元格覆盖的每一格都指向同一来源框。"""
    raw = table.extract()
    cells = [([round(v, 2) for v in box], cell_text(raw[r][c]), r + 1, c + 1)
             for r, row in enumerate(table.rows) for c, box in enumerate(row.cells)
             if box is not None]
    xs = sorted({coord for box, *_ in cells for coord in (box[0], box[2])})
    ys = sorted({coord for box, *_ in cells for coord in (box[1], box[3])})
    grid = []
    for top, bottom in pairwise(ys):
        row = []
        for left, right in pairwise(xs):
            x, y = (left + right) / 2, (top + bottom) / 2
            match = next(((value, r, c, box) for box, value, r, c in cells
                          if box[0] <= x < box[2] and box[1] <= y < box[3]),
                         ("", None, None, None))
            row.append({"value": match[0], "source_row": match[1], "source_column": match[2],
                        "bbox": match[3]})
        grid.append(row)
    return {"grid": grid, "column_positions": xs}


def span(grid, r, c):
    """单元格自 (r, c) 起向下、向右覆盖的网格数。"""
    box = grid[r][c]["bbox"]
    if box is None:
        return 1, 1
    rows = cols = 1
    while r + rows < len(grid) and grid[r + rows][c]["bbox"] == box:
        rows += 1
    while c + cols < len(grid[r]) and grid[r][c + cols]["bbox"] == box:
        cols += 1
    return rows, cols


def header_depth(grid):
    """表头行数：由顶部合并单元格撑开，且不越过首个含纯数字单元格的行。

    纵向合并的表头格占满整个表头；有文字的横向合并格是分组名，其下至少还有一行子表头。
    首行就出现纯数字时视为没有表头。
    """
    depth, r = 1, 0
    while r < min(depth, 5, len(grid)):
        for c, cell in enumerate(grid[r]):
            rows, cols = span(grid, r, c)
            depth = max(depth, r + rows + (cols > 1 and bool(cell["value"])))
        r += 1
    numeric = next((i for i, row in enumerate(grid[:5])
                    if any(NUMERIC.fullmatch(cell["value"]) for cell in row)), None)
    if numeric is not None:
        depth = min(depth, numeric)
    return min(depth, len(grid))


def read_caption(section):
    """紧邻表格之前的短正文：含“表”字作表题，“单位：”之后作单位。"""
    text = section["content"]
    unit = UNIT.search(text)
    sources = [{"page": s["page"], "block_id": s.get("block_id"), "start": s["start"], "end": s["end"]}
               for s in section["source_spans"] if "单位" in text[s["start"]:s["end"]]]
    return (UNIT.sub("", text).strip() if "表" in text else None,
            unit[1].strip() if unit else None, sources if unit else [])


def repeated_header(table, grid):
    """续页重复打印的表头不作数据行。"""
    depth = len(table["header_cells"][0]["cells"])
    if not depth or len(grid) < depth:
        return 0
    same = all(grid[r][c]["value"] == column["cells"][r]["value"]
               for c, column in enumerate(table["header_cells"]) for r in range(depth))
    return depth if same else 0


def inherit_keys(table, body, bottom):
    """分页截断的合并单元格在续页显示为空白：只延续行首连续空白的行标识列。

    行标识列须在此前每一数据行都有值，且除首列序号外不是数值列；
    金额等数值列和本来就有空白的列从不填充。
    """
    if not table["rows"] or not body:
        return
    last = table["rows"][-1]
    top = min(cell["bbox"][1] for cell in body[0] if cell["bbox"])
    for col, first in enumerate(body[0]):
        if first["value"]:
            break
        values = [row["cells"][col]["value"] for row in table["rows"]]
        measure = col and sum(bool(NUMERIC.fullmatch(v)) for v in values) * 2 > len(values)
        origin = last["cells"][col]
        if (not all(values) or measure or not first["bbox"] or not origin["bbox"]
                or abs(first["bbox"][1] - top) > TOLERANCE
                or abs(origin["bbox"][3] - bottom) > TOLERANCE):
            continue
        source = origin.get("inherited_from") or {
            "page": last["page"], "bbox": origin["bbox"], "row": origin["source_row"],
            "column": origin["source_column"], "value": origin["value"]}
        for row in body:
            cell = row[col]
            if cell["value"] or cell["bbox"] != first["bbox"]:
                break
            cell.update(raw_value="", value=origin["value"], inherited_from=source,
                        inheritance="跨页合并单元格延续：同表同列、页首空白接续上页页尾")


def serial_column(rows):
    """首列为 1、2、3… 的连续序号时，仅作行标识，不进入检索文本。"""
    values = list(dict.fromkeys(v for row in rows if (v := row["cells"][0]["value"])))
    return len(values) >= 2 and values == [str(i) for i in range(1, len(values) + 1)]


def new_table(number, grid, page, caption):
    depth = header_depth(grid)
    title, unit, unit_sources = read_caption(caption) if caption else (None, None, [])
    names = [" / ".join(dict.fromkeys(row[col]["value"] for row in grid[:depth]
                                      if row[col]["value"])) or f"未识别字段（列 {col + 1}）"
             for col in range(len(grid[0]))]
    # 同名父级表头横跨多列时保留列号，避免把不同字段拼成一个。
    columns = [f"{name}（列 {i + 1}）" if names.count(name) > 1 else name
               for i, name in enumerate(names)]
    return {"id": f"t{number}", "title": title, "unit": unit, "unit_sources": unit_sources,
            "columns": columns, "header_pages": [page],
            "header_cells": [{"column": col + 1, "path": names[col], "page": page,
                              "cells": [row[col] for row in grid[:depth]]}
                             for col in range(len(grid[0]))],
            "fragments": [], "rows": []}


def build_tables(sections):
    """按阅读顺序把表格片段归入逻辑表，并在片段所在段落上记录 table_id。

    续表条件：两个片段在段落序列中紧邻（中间没有任何正文）、位于相邻页且列边界一致。
    """
    tables = []
    for index, section in enumerate(sections):
        if section["kind"] != "table":
            continue
        data = section["table_data"]
        grid = [[dict(cell) for cell in row] for row in data["grid"]]
        page = section["pages"][0]
        before = sections[index - 1] if index else None
        previous = tables[-1]["fragments"][-1] if tables else None
        continued = (before is not None and before["kind"] == "table"
                     and previous["page"] + 1 == page
                     and len(previous["positions"]) == len(data["column_positions"])
                     and all(abs(a - b) < TOLERANCE for a, b in
                             zip(previous["positions"], data["column_positions"])))
        if continued:
            table = tables[-1]
            header_rows = repeated_header(table, grid)
            if header_rows:
                table["header_pages"].append(page)
            inherit_keys(table, grid[header_rows:], previous["bottom"])
        else:
            caption = (before if before is not None and before["kind"] == "text"
                       and len(before["content"]) < CAPTION_LIMIT else None)
            table = new_table(len(tables) + 1, grid, page, caption)
            if caption and (table["title"] or table["unit"]):
                caption["caption_of"] = table["id"]
            header_rows = len(table["header_cells"][0]["cells"])
            tables.append(table)
        offset = sum(f["row_count"] for f in table["fragments"])
        table["rows"] += [{"row": offset + number, "page": page, "section_id": section["id"],
                           "continued": continued, "cells": row}
                          for number, row in enumerate(grid[header_rows:], header_rows + 1)]
        table["fragments"].append({
            "section_id": section["id"], "page": page, "header_rows": header_rows,
            "row_count": len(grid), "positions": data["column_positions"],
            "bottom": max(cell["bbox"][3] for row in grid for cell in row if cell["bbox"])})
        section["table_id"] = table["id"]
    for table in tables:
        table["serial"] = serial_column(table["rows"])
    return tables
