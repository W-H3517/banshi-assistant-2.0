"""把阅读单元切成不超过嵌入模型输入上限的检索片段，并映射回原 PDF 的高亮区域。"""

from .structure import source_path
from .table_text import row_retrieval_text, table_card, unit_suffix, whole_table_text

# 纯标题不单独建片段，作为所属信息。表格：小表一个整表片段，各行片段作检索入口、命中时返回整表；
# 大表一张信息卡加逐行片段。
NOT_INDEXED = ("chapter", "article")
TABLE_WHOLE_TOKENS = 400
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


def table_regions(table, pages):
    """整表高亮区域：表头与各行单元格按页取外接矩形。"""
    boxes = {}
    for column in table["header_cells"]:
        for cell in column["cells"]:
            if cell["bbox"] is not None:
                boxes.setdefault(column["page"], []).append(cell["bbox"])
    for row in table["rows"]:
        for cell in row["cells"]:
            if cell["bbox"] is not None and "inherited_from" not in cell:
                boxes.setdefault(row["page"], []).append(cell["bbox"])
    regions = [rect for page, items in sorted(boxes.items()) if (rect := region(
        (min(b[0] for b in items), min(b[1] for b in items),
         max(b[2] for b in items), max(b[3] for b in items)), pages[page - 1]))]
    return located(regions, len(regions), len(boxes))


def table_texts(structure, tables, by_id, embeddings):
    """每张表的检索文本：小表整表一片，大表为信息卡；返回 {表ID: (文本, 是否整表, 行前缀)}。"""
    texts = {}
    for node in structure["nodes"]:
        if node["kind"] != "table":
            continue
        table = tables[node["table_id"]]
        chapter, article = source_path(by_id, node)
        path = " / ".join(filter(None, (chapter, article)))
        whole = whole_table_text(table, node["label"], path)
        prefix = f"{node['label']}{unit_suffix(table)}\n"  # 行片段前缀：表名与单位
        if len(embeddings.tokenizer.encode(whole).ids) <= TABLE_WHOLE_TOKENS:
            texts[table["id"]] = (whole, True, prefix)
        else:
            texts[table["id"]] = (table_card(table, node["label"], path), False, prefix)
    return texts


def build_chunks(structure, tables, pages, title, embeddings):
    by_id = {node["id"]: node for node in structure["nodes"]}
    rows = {(table["id"], row["row"]): (table, row) for table in tables for row in table["rows"]}
    units = {unit["id"]: unit for unit in structure["units"]}
    tables_by_id = {table["id"]: table for table in tables}
    table_text = table_texts(structure, tables_by_id, by_id, embeddings)
    chunks = []
    for unit in structure["units"]:
        # 帽句不单独检索：它随每个分点片段出现。
        if unit["kind"] in NOT_INDEXED or "caption_of" in unit or unit.get("leads"):
            continue
        node = by_id[unit["node_id"]]
        table, row = (rows[unit["table_ref"]["table"], unit["table_ref"]["row"]]
                      if unit["kind"] == "table_row" else (None, None))
        # 表格行片段只作检索入口，都归到表格节点：同一张表在搜索结果里只占一个名额。
        # 小表命中时返回整张表；大表返回信息卡与按查询词筛出的行。
        whole_row = row is not None and table_text[table["id"]][1]
        if row is not None:
            node = by_id[node["parent_id"]]
        lead, text = "", unit["content"]
        if "lead_unit_id" in unit:
            lead = units[unit["lead_unit_id"]]["content"].strip() + "\n"
        elif row:
            lead, text = table_text[table["id"]][2], row_retrieval_text(table, row)
        elif unit["kind"] == "table":
            table = tables_by_id[unit["table_ref"]["table"]]
            text = table_text[table["id"]][0]
        reserved = len(embeddings.tokenizer.encode(lead, add_special_tokens=False).ids) if lead else 0
        parts = embeddings.split(text, reserved)
        for part in parts:
            start, end = part["start"], part["end"]
            # 标题只在正文开头：只有首个片段可省去路径中的该标题，拆出的后续片段仍需路径。
            chapter, article = source_path(by_id, node, unit.get("body_heading_id") if start == 0 else None)
            if row:
                regions, status = row_regions(row, pages)
                chunk_pages = [row["page"]]
                start, end = (0, node["length"]) if whole_row else (0, 0)
            elif table:
                regions, status = table_regions(table, pages)
                chunk_pages = node["pages"]
                # 整表片段覆盖整个阅读单元；信息卡只是摘要，需查表取得具体行。
                start, end = 0, node["length"] if table_text[table["id"]][1] else 0
            else:
                regions, status = text_regions(unit, start, end, pages)
                chunk_pages = sorted({s["page"] for s in unit["spans"]
                                      if s["start"] < end and s["end"] > start})
            chunk = {"ordinal": len(chunks), "node_id": node["id"], "unit_id": unit["id"],
                     "kind": unit["kind"],
                     "title": article or (node["label"] if table else "正文段落"),
                     "chapter": chapter,
                     "source": {"document_title": title, "filename": structure["filename"],
                                "chapter": chapter, "article": article},
                     "pages": chunk_pages, "content": lead + part["content"], "start": start, "end": end,
                     "token_count": len(embeddings.tokenizer.encode(lead + part["content"]).ids),
                     "regions": regions, "location_status": status,
                     "warnings": node["warnings"] + ([SPLIT_WARNING] if len(parts) > 1 else []),
                     "boundary_status": node["boundary_status"],
                     "boundary_reasons": node["boundary_reasons"]}
            if row:
                chunk.update(row=row["row"], header_pages=table["header_pages"])
                if whole_row:
                    chunk["expanded"] = table_text[table["id"]][0]
            elif table:
                chunk.update(table_whole=table_text[table["id"]][1], header_pages=table["header_pages"])
            chunks.append(chunk)
    return chunks
