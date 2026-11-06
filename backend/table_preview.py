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
            match = next(((value, r, c) for (x0, y0, x1, y1), value, r, c in cells
                          if x0 <= x < x1 and y0 <= y < y1), ("", None, None))
            row.append({"value": match[0], "source_row": match[1], "source_column": match[2]})
        grid.append(row)
    return {"grid": grid, "column_positions": xs, "raw_rows": raw}


def describe_tables(sections, document_title):
    tables, previous = [], None
    caption = ""
    for section in sections:
        if section["kind"] != "table":
            caption = section["content"] if section["kind"] == "text" else ""
            continue
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
            status = "疑似续表：沿用相邻页候选表头，需确认同属一张表；空白数据未跨页继承。"
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
            title = caption or section["chapter"]
            match = re.search(r"单位\s*[:：]\s*([^\n]+)", caption)
            unit = match[1].strip() if match else "未明确识别，请核对表头或注释"
            status = "本页候选表头：根据表格网格和首批数据行识别，请核对字段层级及单位。"
        # 同名父级表头横跨多列时保留列号，避免把不同字段拼成一个。
        labels = [f"{label}（列 {i + 1}）" if headers.count(label) > 1 else label
                  for i, label in enumerate(headers)]
        descriptions = []
        for row_index, row in enumerate(grid[header_rows:], header_rows + 1):
            fields = [{"name": label, **cell} for label, cell in zip(labels, row)]
            body = "；".join(f"{f['name']}：{f['value'] or '空白（未推断）'}" for f in fields)
            descriptions.append({"row": row_index, "page": page, "fields": fields,
                                 "content": f"制度：{document_title}\n表：{title}\n单位：{unit}\n"
                                 f"PDF 第 {page} 页，表格第 {row_index} 行。\n{body}",
                                 "needs_review": True})
        tables.append({"section_id": section["id"], "page": page, "title": title,
                       "unit": unit, "headers": labels, "header_pages": header_pages,
                       "header_rows": header_rows, "status": status,
                       "rows": descriptions, "needs_review": True})
        previous = {"page": page, "chapter": section["chapter"], "headers": headers,
                    "header_pages": header_pages, "positions": data["column_positions"],
                    "title": title, "unit": unit}
        caption = ""
    return tables
