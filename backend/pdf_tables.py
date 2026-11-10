"""有来源的表格行描述；只展开实际合并单元格，不前向填充空白。"""

import re
from itertools import pairwise


def cell_text(value):
    return re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "",
                  (value or "").replace("\n", " ")).strip()


def extract_grid(table):
    raw = table.extract()
    cells = [(box, cell_text(raw[r][c]), r + 1, c + 1)
             for r, row in enumerate(table.rows) for c, box in enumerate(row.cells)
             if box is not None]
    xs = sorted({coord for box, *_ in cells for coord in (box[0], box[2])})
    ys = sorted({coord for box, *_ in cells for coord in (box[1], box[3])})
    grid = []
    for top, bottom in pairwise(ys):
        row = []
        for left, right in pairwise(xs):
            x, y = (left + right) / 2, (top + bottom) / 2
            match = next(((value, r, c, (x0, y0, x1, y1)) for (x0, y0, x1, y1), value, r, c in cells
                          if x0 <= x < x1 and y0 <= y < y1), ("", None, None, None))
            row.append({"value": match[0], "source_row": match[1], "source_column": match[2], "bbox": match[3]})
        grid.append(row)
    return {"grid": grid, "column_positions": xs, "raw_rows": raw}


def describe_tables(sections, document_title):
    tables, previous = [], None
    caption = ""
    caption_sections = []
    for section in sections:
        if section["kind"] != "table":
            if section["kind"] == "text":
                caption = section["content"]
                caption_sections = [section]
            else:
                caption, caption_sections = "", []
            continue
        for caption_section in caption_sections:
            if "表" in caption_section["content"] and len(caption_section["content"]) < 250:
                caption_section["retrieval_excluded"] = True
        data = section["table_data"]
        grid = data["grid"]
        width = len(grid[0])
        page = section["pages"][0]
        same_columns = previous and len(previous["positions"]) == len(data["column_positions"]) and all(
            abs(a - b) < 2 for a, b in zip(previous["positions"], data["column_positions"]))
        continuation = (previous and same_columns and previous["page"] + 1 == page
                        and previous["chapter"] == section["chapter"] and not caption)
        if continuation:
            headers = previous["headers"]
            header_pages = previous["header_pages"]
            header_rows = 0
            title, unit = previous["title"], previous["unit"]
            unit_sources = previous["unit_sources"]
            status = "相邻页列边界一致且无新标题，继承表头；跨页空白数据不推断。"
        else:
            # 用出现数值数据的首行估计表头深度；结果显式标注为候选，不视为已审核。
            first_data = next((i for i, row in enumerate(grid[:5]) if any(
                re.fullmatch(r"\d+(?:\.\d+)?", cell["value"]) for cell in row)), None)
            header_rows = first_data if first_data is not None else 1
            headers = []
            for col in range(width):
                parts = list(dict.fromkeys(row[col]["value"] for row in grid[:header_rows]
                                          if row[col]["value"]))
                headers.append(" / ".join(parts) or f"未识别字段（列 {col + 1}）")
            header_pages = [page]
            title = caption if "表" in caption else section["chapter"]
            match = re.search(r"单位\s*[:：]\s*([^\n]+)", caption)
            unit = match[1].strip() if match else "未明确识别，请核对表头或注释"
            unit_sources = [{"page": s["page"], "block_id": s.get("block_id"),
                             "start": s["start"], "end": s["end"]}
                            for c in caption_sections for s in c["source_spans"]
                            if "单位" in c["content"][s["start"]:s["end"]]]
            status = "本页候选表头：根据表格网格和首批数据行识别，请核对字段层级及单位。"
        table_id = previous["table_id"] if continuation else f"table:{section['id']}"
        header_cells = previous["header_cells"] if continuation else [
            {"column": col + 1, "path": headers[col], "page": page,
             "cells": [row[col] for row in grid[:header_rows]]} for col in range(width)]
        # 同名父级表头横跨多列时保留列号，避免把不同字段拼成一个。
        labels = [f"{label}（列 {i + 1}）" if headers.count(label) > 1 else label
                  for i, label in enumerate(headers)]
        descriptions = []
        for row_index, row in enumerate(grid[header_rows:], header_rows + 1):
            fields = [{"name": label, **cell} for label, cell in zip(labels, row)]
            body = "；".join(f"{f['name']}：{f['value'] or '空白（未推断）'}" for f in fields)
            descriptions.append({"row": row_index, "page": page, "fields": fields,
                                 "content": f"表：{title}\n单位：{unit}\n"
                                 f"{body}",
                                 "table_id": table_id, "unit": unit, "row_key": [f["value"] for f in fields[:3]],
                                 "header_cells": header_cells, "needs_review": True})
        tables.append({"section_id": section["id"], "page": page, "title": title,
                       "unit": unit, "headers": labels, "header_pages": header_pages,
                       "header_rows": header_rows, "status": status,
                       "rows": descriptions, "table_id": table_id, "header_cells": header_cells,
                       "continuation": bool(continuation), "needs_review": True})
        tables[-1]["unit_sources"] = unit_sources
        previous = {"page": page, "chapter": section["chapter"], "headers": headers,
                    "header_pages": header_pages, "positions": data["column_positions"],
                    "title": title, "unit": unit, "table_id": table_id, "header_cells": header_cells,
                    "unit_sources": unit_sources}
        caption = ""
    return tables
