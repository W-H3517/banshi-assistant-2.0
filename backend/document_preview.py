"""只读转换试验：连续条款、来源页码及候选切分，不修改现行索引。"""

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


def table_markdown(rows):
    width = max(map(len, rows))
    # 不将续表第一行误认作表头，不猜测合并单元格继承关系。
    header = "| " + " | ".join(f"列 {i + 1}" for i in range(width)) + " |"
    lines = [header, "| " + " | ".join("---" for _ in range(width)) + " |"]
    for row in rows:
        cells = [(cell or "").replace("\n", " ").replace("|", "\\|") for cell in row]
        lines.append("| " + " | ".join(cells + [""] * (width - len(cells))) + " |")
    return "\n".join(lines)


def convert_pdf(raw):
    extract_pages(raw)  # 沿用文字 PDF 校验，扫描件不进入此预览。
    records, removed, warnings = [], [], []
    with pdfplumber.open(BytesIO(raw)) as pdf:
        pages = []
        for number, original in enumerate(pdf.pages, 1):
            page = original.dedupe_chars()
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
                             "kind": "text"} for line in lines]
            for table in tables:
                rows = table.extract()
                page_records.append({"page": number, "text": table_markdown(rows),
                                     "top": table.bbox[1], "edge": False, "kind": "table",
                                     "table_data": extract_grid(table)})
                warnings.append({"page": number, "message":
                    "原网格逐页保留；表格行描述会展开同页实际合并单元格，候选表头与续表关系仍需核对，跨页空白未补齐。"})
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

    def append(text, page, separator=""):
        start = len(current["content"])
        current["content"] += separator + text
        current["source_spans"].append({"start": start, "end": len(current["content"]),
                                        "page": page})

    for row in records:
        text = normalize_line(row["text"]) if row["kind"] == "text" else row["text"]
        article = ARTICLE.match(text)
        # normalize_line 会去掉章节编号与标题之间的中文空格。
        is_chapter = re.match(rf"^第\s*{NUMBER}\s*[章节]", text)
        if row["kind"] == "table":
            begin(f"表格（PDF 第 {row['page']} 页）", "table")
            current["table_data"] = row["table_data"]
            append(text, row["page"])
            current = None
        elif is_chapter or ANNEX.fullmatch(text):
            chapter = text
            begin(text, "heading")
            append(text, row["page"])
        elif article:
            begin(article[1], "article")
            append(text, row["page"])
        elif re.match(r"^附件\s*[:：]", text):
            begin("附件目录", "text")
            append(text, row["page"])
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
            append(text, row["page"], separator)

    markdown = []
    for section in sections:
        section["pages"] = sorted({span["page"] for span in section["source_spans"]})
        prefix = "## " if section["kind"] == "heading" else ""
        if section["kind"] == "article":
            match = ARTICLE.match(section["content"])
            markdown.append(f"### {match[1]}\n\n{match[2].lstrip()}")
        elif section["kind"] == "table":
            markdown.append(f"### {section['title']}\n\n{section['content']}")
        else:
            markdown.append(prefix + section["content"])
    warnings.insert(0, {"page": None, "message":
        "规则转换试验：请核对条款层级、跨页接续和被移除文字。引用追查为候选关联，不代表适用性判断。"})
    return {"markdown": "\n\n".join(markdown), "sections": sections,
            "removed_lines": removed, "warnings": warnings}


def preview_document(raw, title, embeddings):
    result = convert_pdf(raw)
    result["tables"] = describe_tables(result["sections"], title)
    chunks = []
    for section in result["sections"]:
        if section["kind"] == "table":
            table = next(t for t in result["tables"] if t["section_id"] == section["id"])
            for row in table["rows"]:
                count = len(embeddings.tokenizer.encode(title[:60] + "\n" + row["content"]).ids)
                chunks.append({"ordinal": len(chunks), "section_id": section["id"],
                               "title": section["title"], "chapter": section["chapter"],
                               "kind": "table_row", "pages": [row["page"]],
                               "header_pages": table["header_pages"], "row": row["row"],
                               "content": row["content"], "token_count": count,
                               "needs_review": True, "over_budget": count > 512})
            continue
        # 与现有模型使用相同长度预算；切分只在本条款内展开，可跨物理页。
        cursor = 0
        for chunk in embeddings.split(title, [section["content"]]):
            start = section["content"].index(chunk["content"], cursor)
            end = start + len(chunk["content"])
            cursor = start + 1
            pages = sorted({span["page"] for span in section["source_spans"]
                            if span["start"] < end and span["end"] > start})
            chunks.append({"ordinal": len(chunks), "section_id": section["id"],
                           "title": section["title"], "chapter": section["chapter"],
                           "kind": section["kind"], "pages": pages,
                           "content": chunk["content"], "start": start, "end": end,
                           "token_count": len(embeddings.tokenizer.encode(
                               title[:60] + "\n" + chunk["content"]).ids)})
    return {**result, "chunks": chunks, "version": "structure-preview-v2",
            "active_for_search": False}
