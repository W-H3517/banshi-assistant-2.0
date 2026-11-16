"""把阅读单元切成不超过嵌入模型输入上限的检索片段，并映射回原 PDF 的高亮区域。"""

from .structure import source_path
from .table_text import row_retrieval_text

# 纯标题、整表网格不单独建片段：标题作为所属信息，表格按行检索。
NOT_INDEXED = ("chapter", "article", "table")
SPLIT_WARNING = "原段落或表格行超过输入预算，已拆分，可按所属阅读单元补读完整内容"


def region(box, page):
    """PDF 坐标框转为相对页面可视区域的比例矩形；完全在可视区域外时返回 None。"""
    left, top, right, bottom = page["view_box"]
    x0, y0, x1, y1 = box
    x0, y0, x1, y1 = max(x0, left), max(y0, top), min(x1, right), min(y1, bottom)
    if x1 <= x0 or y1 <= y0:
        return None
    return {"page": page["number"], "x": (x0 - left) / page["width"],
            "y": (y0 - top) / page["height"], "width": (x1 - x0) / page["width"],
            "height": (y1 - y0) / page["height"]}


def located(regions, mapped, expected):
    return regions, "complete" if mapped == expected and expected else "partial" if mapped else "unlocated"


def text_regions(unit, start, end, pages):
    """正文片段 [start, end) 的高亮区域：每个来源行一个矩形，只覆盖片段内的字符。"""
    regions, mapped, expected = [], 0, 0
    for span in unit["spans"]:
        lo, hi = max(start, span["start"]), min(end, span["end"])
        if lo >= hi:
            continue
        text = unit["content"][lo:hi]
        boxes = [box for char, box in zip(text, span["boxes"][lo - span["start"]:hi - span["start"]])
                 if not char.isspace() and box]
        expected += sum(not char.isspace() for char in text)
        if boxes:
            rect = region((min(b[0] for b in boxes), min(b[1] for b in boxes),
                           max(b[2] for b in boxes), max(b[3] for b in boxes)),
                          pages[span["page"] - 1])
            if rect:
                regions.append(rect)
                mapped += len(boxes)
    return located(regions, mapped, expected)


def row_regions(row, pages):
    """表格行的高亮区域：本行实际来源单元格，不含承接上页的继承值。"""
    boxes = list(dict.fromkeys(tuple(cell["bbox"]) for cell in row["cells"]
                               if cell["bbox"] is not None and "inherited_from" not in cell))
    regions = [rect for box in boxes if (rect := region(box, pages[row["page"] - 1]))]
    return located(regions, len(regions), len(boxes))


def build_chunks(structure, tables, pages, title, embeddings):
    by_id = {node["id"]: node for node in structure["nodes"]}
    rows = {(table["id"], row["row"]): (table, row) for table in tables for row in table["rows"]}
    chunks = []
    for unit in structure["units"]:
        if unit["kind"] in NOT_INDEXED or "caption_of" in unit:
            continue
        node = by_id[unit["node_id"]]
        chapter, article = source_path(by_id, node)
        table, row = rows[unit["table_ref"]["table"], unit["table_ref"]["row"]] \
            if unit["kind"] == "table_row" else (None, None)
        parts = embeddings.split(row_retrieval_text(table, row) if row else unit["content"])
        for part in parts:
            start, end = part["start"], part["end"]
            if row:
                regions, status = row_regions(row, pages)
                chunk_pages = [row["page"]]
            else:
                regions, status = text_regions(unit, start, end, pages)
                chunk_pages = sorted({s["page"] for s in unit["spans"]
                                      if s["start"] < end and s["end"] > start})
            chunk = {"ordinal": len(chunks), "node_id": node["id"], "unit_id": unit["id"],
                     "kind": unit["kind"],
                     "title": article or (by_id[node["parent_id"]]["label"] if row else "正文段落"),
                     "chapter": chapter,
                     "source": {"document_title": title, "filename": structure["filename"],
                                "chapter": chapter, "article": article},
                     "pages": chunk_pages, "content": part["content"], "start": start, "end": end,
                     "token_count": len(embeddings.tokenizer.encode(part["content"]).ids),
                     "regions": regions, "location_status": status,
                     "warnings": node["warnings"] + ([SPLIT_WARNING] if len(parts) > 1 else []),
                     "boundary_status": node["boundary_status"],
                     "boundary_reasons": node["boundary_reasons"]}
            if row:
                chunk.update(row=row["row"], header_pages=table["header_pages"])
            chunks.append(chunk)
    return chunks
