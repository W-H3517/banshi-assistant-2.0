"""只读候选切分：保留原文坐标供 PDF 高亮，不修改现行索引。"""

import re
from collections import Counter
from io import BytesIO

import pdfplumber

from .pdf import extract_pages
from .table_preview import describe_tables, extract_grid

NUMBER = r"[零〇一二三四五六七八九十百千两\d]+"
ARTICLE = re.compile(rf"^(第\s*{NUMBER}\s*条)(.*)$", re.DOTALL)
ANNEX = re.compile(r"^附件\s*\d+\s*$")
PAGE_NUMBER = re.compile(r"^(?:[-—–]\s*\d+\s*[-—–]|第\s*\d+\s*页(?:\s*共\s*\d+\s*页)?)$")


def normalize_line(text):
    # PDF 字距常被提取为中文间的空格；保留英文、数字之间的空格。
    return re.sub(r"(?<=[\u4e00-\u9fff]) +(?=[\u4e00-\u9fff])", "", text.strip())


def line_sources(line):
    """将规范化后的文字逐字关联到原 PDF；不匹配时不猜测坐标。"""
    text = normalize_line(line["text"])
    chars = [(letter, (char["x0"], char["top"], char["x1"], char["bottom"]))
             for char in line["chars"] for letter in char["text"] if not letter.isspace()]
    if "".join(letter for letter, _ in chars) != "".join(text.split()):
        return text, [None] * len(text)
    boxes = iter(box for _, box in chars)
    return text, [None if letter.isspace() else next(boxes) for letter in text]


def region(box, page):
    left, top, right, bottom = page["view_box"]
    x0, y0, x1, y1 = box
    x0, y0, x1, y1 = max(x0, left), max(y0, top), min(x1, right), min(y1, bottom)
    if x1 <= x0 or y1 <= y0:
        return None
    return {"page": page["number"], "x": (x0 - left) / page["width"],
            "y": (y0 - top) / page["height"], "width": (x1 - x0) / page["width"],
            "height": (y1 - y0) / page["height"]}


def text_regions(section, start, end, pages):
    regions, mapped, expected = [], 0, 0
    for span in section["source_spans"]:
        lo, hi = max(start, span["start"]), min(end, span["end"])
        if lo >= hi:
            continue
        text = section["content"][lo:hi]
        if lo == span["start"] and hi == span["end"] and span.get("bbox"):
            count = sum(not char.isspace() for char in text)
            expected += count
            rect = region(span["bbox"], pages[span["page"] - 1])
            if rect:
                regions.append(rect)
                mapped += count
            continue
        boxes = span["boxes"][lo - span["start"]:hi - span["start"]]
        expected += sum(not char.isspace() for char in text)
        valid = [box for char, box in zip(text, boxes) if not char.isspace() and box]
        if valid:
            box = (min(b[0] for b in valid), min(b[1] for b in valid),
                   max(b[2] for b in valid), max(b[3] for b in valid))
            rect = region(box, pages[span["page"] - 1])
            if rect:
                regions.append(rect)
                mapped += len(valid)
    status = "complete" if mapped == expected and expected else "partial" if mapped else "unlocated"
    return regions, status


def convert_pdf(raw):
    extract_pages(raw)  # 文字 PDF 候选预览；扫描 PDF 直接读取入库时保存的解析快照。
    records, removed, page_info = [], [], []
    with pdfplumber.open(BytesIO(raw)) as pdf:
        pages = []
        for number, original in enumerate(pdf.pages, 1):
            page = original.dedupe_chars()
            media, crop = page.bbox, page.cropbox or page.bbox
            view = (max(media[0], crop[0]), max(media[1], crop[1]),
                    min(media[2], crop[2]), min(media[3], crop[3]))
            page_info.append({"number": number, "width": view[2] - view[0],
                              "height": view[3] - view[1], "view_box": view})
            tables = page.find_tables()
            boxes = [table.bbox for table in tables]

            def outside_tables(obj, boxes=boxes):
                x = (obj.get("x0", 0) + obj.get("x1", 0)) / 2
                y = (obj.get("top", 0) + obj.get("bottom", 0)) / 2
                return not any(x0 <= x <= x1 and top <= y <= bottom
                               for x0, top, x1, bottom in boxes)

            lines = page.filter(outside_tables).extract_text_lines(x_tolerance=2)
            page_records = [{"page": number, "text": line["text"], "top": line["top"],
                             "edge": line["top"] < page.height * .1
                             or line["bottom"] > page.height * .9,
                             "kind": "text", "source": line_sources(line)} for line in lines]
            for table in tables:
                rows = table.extract()
                page_records.append({"page": number, "text": "\n".join(" | ".join(cell or "" for cell in row) for row in rows),
                                     "top": table.bbox[1], "edge": False, "kind": "table",
                                     "table_data": extract_grid(table)})
            pages.append(sorted(page_records, key=lambda row: row["top"]))

        edge_counts = Counter(text for page in pages for text in {
            row["text"].strip() for row in page if row["edge"]})
        for page in pages:
            for row in page:
                text = row["text"].strip()
                reason = ""
                if row["edge"] and (PAGE_NUMBER.fullmatch(text) or text == str(row["page"])):
                    reason = "页边页码"
                elif row["edge"] and edge_counts[text] >= max(3, len(pages) * .6):
                    reason = "多页重复的页边文字（请核对是否为页眉页脚）"
                if reason:
                    removed.append({"page": row["page"], "text": text, "reason": reason})
                else:
                    records.append(row)

    sections = []
    current = None
    chapter = "正文"

    def begin(title, kind):
        nonlocal current
        current = {"id": len(sections) + 1, "title": title, "chapter": chapter,
                   "kind": kind, "content": "", "source_spans": []}
        sections.append(current)

    def append(text, page, separator="", boxes=None):
        start = len(current["content"])
        current["content"] += separator + text
        current["source_spans"].append({"start": start, "end": len(current["content"]),
                                        "page": page, "boxes": [None] * len(separator) + (boxes or [None] * len(text))})

    for row in records:
        text, boxes = row["source"] if row["kind"] == "text" else (row["text"], None)
        article = ARTICLE.match(text)
        # normalize_line 会去掉章节编号与标题之间的中文空格。
        is_chapter = re.match(rf"^第\s*{NUMBER}\s*[章节]", text)
        if row["kind"] == "table":
            begin(f"表格（PDF 第 {row['page']} 页）", "table")
            current["table_data"] = row["table_data"]
            append(text, row["page"], boxes=boxes)
            current = None
        elif is_chapter or ANNEX.fullmatch(text):
            chapter = text
            begin(text, "heading")
            append(text, row["page"], boxes=boxes)
        elif article:
            begin(article[1], "article")
            append(text, row["page"], boxes=boxes)
        elif re.match(r"^附件\s*[:：]", text):
            begin("附件目录", "text")
            append(text, row["page"], boxes=boxes)
        else:
            if current is None or current["kind"] == "heading":
                begin("说明 / 段落", "text")
            previous = current["content"]
            separator = ""
            if previous:
                if previous[-1] in "。！？；" or re.match(r"^[（(][一二三四五六七八九十\d]+[）)]", text):
                    separator = "\n\n"
                elif previous[-1].isascii() and text[0].isascii():
                    separator = " "
            append(text, row["page"], separator, boxes)

    for section in sections:
        section["pages"] = sorted({span["page"] for span in section["source_spans"]})
    return {"sections": sections, "pages": page_info, "removed_lines": removed}


def preview_document(raw, title, embeddings):
    result = convert_pdf(raw)
    result["tables"] = describe_tables(result["sections"], title)
    chunks = []
    for section in result["sections"]:
        if section["kind"] == "table":
            table = next(t for t in result["tables"] if t["section_id"] == section["id"])
            for row in table["rows"]:
                count = len(embeddings.tokenizer.encode(title[:60] + "\n" + row["content"]).ids)
                boxes = list(dict.fromkeys(tuple(field["bbox"]) for field in row["fields"]
                                           if field["bbox"] is not None))
                regions = [rect for box in boxes
                           if (rect := region(box, result["pages"][row["page"] - 1]))]
                chunks.append({"ordinal": len(chunks), "section_id": section["id"],
                               "title": section["title"], "chapter": section["chapter"],
                               "kind": "table_row", "pages": [row["page"]],
                               "header_pages": table["header_pages"], "row": row["row"],
                               "content": row["content"], "token_count": count,
                               "needs_review": True, "over_budget": count > 512,
                               "regions": regions, "location_status": "complete" if regions
                               and len(regions) == len(boxes) else "partial" if regions else "unlocated"})
            continue
        # 与现有模型使用相同长度预算；切分只在本条款内展开，可跨物理页。
        cursor = 0
        for chunk in embeddings.split(title, [section["content"]]):
            start = section["content"].index(chunk["content"], cursor)
            end = start + len(chunk["content"])
            cursor = start + 1
            pages = sorted({span["page"] for span in section["source_spans"]
                            if span["start"] < end and span["end"] > start})
            regions, location_status = text_regions(section, start, end, result["pages"])
            chunks.append({"ordinal": len(chunks), "section_id": section["id"],
                           "regions": regions, "location_status": location_status,
                           "title": section["title"], "chapter": section["chapter"],
                           "kind": section["kind"], "pages": pages,
                           "content": chunk["content"], "start": start, "end": end,
                           "token_count": len(embeddings.tokenizer.encode(
                               title[:60] + "\n" + chunk["content"]).ids)})
    return {"pages": result["pages"], "chunks": chunks, "version": "pdf-chunks-v1",
            "removed_line_count": len(result["removed_lines"]), "active_for_search": False}
