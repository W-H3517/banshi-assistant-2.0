"""文字 PDF 结构提取，保留正文、条款、表格及来源坐标。"""

import re
from collections import Counter
from io import BytesIO
from statistics import median

import pdfplumber

from .pdf import extract_pages
from .pdf_tables import extract_grid

NUMBER = r"[零〇一二三四五六七八九十百千两\d]+"
ARTICLE = re.compile(rf"^(第\s*{NUMBER}\s*条)(.*)$", re.DOTALL)
ANNEX = re.compile(r"^附件\s*\d+\s*$")
PAGE_NUMBER = re.compile(r"^(?:[-—–]\s*\d+\s*[-—–]|第\s*\d+\s*页(?:\s*共\s*\d+\s*页)?)$")
TOC_ENTRY = re.compile(r"(?:\.{5,}|…{2,}|·{5,})\s*\d+\s*$")


def heading_level(text):
    """仅识别原文显式层级；带完整句子或规定的编号段落不当纯标题删除。"""
    if TOC_ENTRY.search(text):
        return None
    if re.match(rf"^第\s*{NUMBER}\s*篇", text):
        return 0
    if ANNEX.fullmatch(text) or re.match(rf"^附录\s*{NUMBER}\s*[:：]", text):
        return 0
    if re.match(rf"^第\s*{NUMBER}\s*章", text):
        return 1
    if re.match(rf"^第\s*{NUMBER}\s*节", text):
        return 2
    if len(text) <= 80 and not re.search(r"[。！？；：:]|应当|必须|不得|严禁", text):
        if re.match(r"^[一二三四五六七八九十]+、", text):
            return 3
        if re.match(r"^[（(][一二三四五六七八九十]+[）)]", text):
            return 4
    return None


def article_label(text):
    match = ARTICLE.match(text)
    if match:
        return match[1]
    match = re.match(r"^(\d+[.．、])(?=\D)", text)
    if match:
        first = text.split("。", 1)[0]
        return first if "。" in text and len(first) <= 50 else match[1]
    return ""


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


def convert_pdf(raw, title="", *, ocr=None, scan_pages=None):
    """逐页提取统一页面块，然后一次组装文档；混合PDF的文字页保留网格。"""
    if scan_pages is None:
        scan_pages = []
        extract_pages(raw, scan_pages=scan_pages)
    page_info = []
    with pdfplumber.open(BytesIO(raw)) as pdf:
        for number, original in enumerate(pdf.pages, 1):
            page = original.dedupe_chars()
            media, crop = page.bbox, page.cropbox or page.bbox
            view = (max(media[0], crop[0]), max(media[1], crop[1]),
                    min(media[2], crop[2]), min(media[3], crop[3]))
            info = {"number": number, "width": view[2] - view[0], "height": view[3] - view[1],
                    "view_box": view, "ocr": number in scan_pages, "blocks": []}
            if number in scan_pages:
                if ocr is None:
                    raise ValueError("扫描页需要本地OCR模型")
                for line in ocr.extract_page(raw, number, view):
                    polygon = line["polygon"]
                    box = [min(p[0] for p in polygon), min(p[1] for p in polygon),
                           max(p[0] for p in polygon), max(p[1] for p in polygon)]
                    info["blocks"].append({"kind": "text", "text": line["text"],
                                           "boxes": line["boxes"], "bbox": box,
                                           "confidence": line["confidence"], "method": "ocr"})
            else:
                tables = [t for t in page.find_tables() if len(t.rows) >= 2 and len(t.columns) >= 2]
                table_boxes = [t.bbox for t in tables]

                def outside_tables(obj, boxes=table_boxes):
                    x = (obj.get("x0", 0) + obj.get("x1", 0)) / 2
                    y = (obj.get("top", 0) + obj.get("bottom", 0)) / 2
                    return not any(x0 <= x <= x1 and top <= y <= bottom for x0, top, x1, bottom in boxes)

                lines = page.filter(outside_tables).extract_text_lines(x_tolerance=2)
                body_size = median(c['size'] for c in page.chars) if page.chars else 0
                for line in lines:
                    text, boxes = line_sources(line)
                    size = median(c['size'] for c in line['chars'])
                    kind = "footnote" if size < body_size * .85 and line['top'] > view[1] + info['height'] * .65 else "text"
                    info["blocks"].append({"kind": kind, "text": text, "boxes": boxes,
                                           "bbox": [line["x0"], line["top"], line["x1"], line["bottom"]],
                                           "confidence": None, "method": "native"})
                for table in tables:
                    rows = table.extract()
                    info["blocks"].append({"kind": "table", "text": "\n".join(
                        " | ".join(cell or "" for cell in row) for row in rows),
                        "bbox": list(table.bbox), "table_data": extract_grid(table),
                        "method": "native", "confidence": None})
            info["blocks"].sort(key=lambda block: (block["bbox"][1], block["bbox"][0]))
            for i, block in enumerate(info["blocks"]):
                block.update(id=f"p{number}:b{i}", page=number,
                             edge=block["bbox"][1] < view[1] + info["height"] * .1
                             or block["bbox"][3] > view[1] + info["height"] * .9)
            page_info.append(info)
            original.close()
    return assemble_document(page_info, title)


def assemble_document(pages, title):
    edge_counts = Counter(text for p in pages for text in {
        b["text"].strip() for b in p["blocks"] if b["edge"]})
    removed, records = [], []
    for page in pages:
        for block in page["blocks"]:
            text = block["text"].strip()
            reason = ""
            if block["edge"] and (PAGE_NUMBER.fullmatch(text) or text == str(page["number"])):
                reason, block["kind"] = "页边页码", "page_number"
            elif block["edge"] and edge_counts[text] >= max(3, len(pages) * .6):
                reason, block["kind"] = "多页重复页边文字", "running_header"
            elif TOC_ENTRY.search(text):
                reason, block["kind"] = "目录导航，原页保留用于核验", "toc_entry"
            elif block["kind"] == "footnote":
                reason = "独立脚注，原页保留用于核验"
            if reason:
                block["excluded_reason"] = reason
                removed.append({"id": block["id"], "page": page["number"], "text": text, "reason": reason})
            elif text:
                records.append(block)
    sections, current = [], None
    chapter = ""

    def finish(boundary):
        if current is not None:
            current["end_boundary"] = boundary

    def begin(label, kind, boundary="explicit_next_unit"):
        nonlocal current
        finish(boundary)
        current = {"id": len(sections) + 1, "title": label, "chapter": chapter,
                   "kind": kind, "content": "", "source_spans": [],
                   "start_boundary": "explicit" if kind in ("article", "heading", "document_title", "table") else "layout",
                   "end_boundary": "unknown"}
        sections.append(current)

    def append(block, separator=""):
        text = block["text"]
        start = len(current["content"])
        current["content"] += separator + text
        current["source_spans"].append({"start": start, "end": len(current["content"]),
            "page": block["page"], "block_id": block["id"], "method": block["method"],
            "confidence": block["confidence"],
            "boxes": [None] * len(separator) + block.get("boxes", [None] * len(text))})

    for row in records:
        text = row["text"]
        article = article_label(text)
        is_title = bool(title) and "".join(text.split()) == "".join(title.split())
        is_chapter = heading_level(text) is not None
        if row["kind"] == "table":
            begin(f"表格（PDF 第 {row['page']} 页）", "table")
            current["table_data"] = row["table_data"]
            current["table_bbox"] = row["bbox"]
            append(row)
            finish("table_grid")
            current = None
        elif is_title:
            begin(text, "document_title")
            append(row)
        elif is_chapter:
            chapter = text
            begin(text, "heading")
            append(row)
        elif article:
            begin(article, "article")
            append(row)
        elif re.match(r"^附件\s*[:：]", text):
            begin("附件目录", "text")
            append(row)
        else:
            if current is None or current["kind"] in ("heading", "document_title") or (
                current["kind"] == "text" and current["content"].endswith(("。", "！", "？"))
                and current["source_spans"][-1]["page"] != row["page"]):
                begin("说明 / 段落", "text", "layout")
            previous = current["content"]
            separator = ""
            if previous:
                if previous[-1] in "。！？；" or re.match(r"^[（(][一二三四五六七八九十\d]+[）)]", text):
                    separator = "\n\n"
                elif previous[-1].isascii() and text[0].isascii():
                    separator = " "
            append(row, separator)
    finish("document_end")
    for section in sections:
        section["pages"] = sorted({span["page"] for span in section["source_spans"]})
        section["block_ids"] = list(dict.fromkeys(s["block_id"] for s in section["source_spans"]))
        status = "confirmed"
        reasons = []
        if section["end_boundary"] == "document_end" and not section["content"].rstrip().endswith(tuple("。！？.!?》）)")):
            status, reasons = "uncertain", ["文档末尾没有明确正文结束标记"]
        if section["end_boundary"] == "layout":
            status, reasons = "uncertain", reasons + ["仅依据版面分段，未见明确的下一条结构标记"]
        if any(b - a > 1 for a, b in zip(section["pages"], section["pages"][1:])):
            status, reasons = "uncertain", reasons + ["正文跨越未纳入的页面，需核对原页"]
        section["boundary_status"], section["boundary_reasons"] = status, reasons
    return {"sections": sections, "pages": pages, "removed_lines": removed}
