"""把页面块组装成连续段落：剔除页边与目录，条款、问答跨页不断开，并记录边界证据。"""

import re
from collections import Counter

from .markers import EFFECT_START, PAGE_NUMBER, TOC_ENTRY, article_label, heading_level

SENTENCE_END = ("。", "！", "？")
EXPLICIT_START = ("article", "heading", "document_title", "table")


def exclude_blocks(pages):
    """标出不进入正文的块（页码、重复页眉、目录、脚注及版面识别出的封面落款），返回其余块。"""
    edge_counts = Counter(text for page in pages for text in {
        block["text"].strip() for block in page["blocks"] if block["edge"]})
    kept, removed = [], []
    for page in pages:
        for block in page["blocks"]:
            text = block["text"].strip()
            reason = block.get("excluded_reason", "")
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
                removed.append({"id": block["id"], "page": page["number"], "text": text,
                                "reason": reason})
            elif text:
                kept.append(block)
    return kept, removed


def left_margins(pages):
    """每页正文左边界（取第 20 百分位），用于判断首行缩进。"""
    margins = {}
    for page in pages:
        xs = sorted(block["bbox"][0] for block in page["blocks"]
                    if block["kind"] == "text" and block.get("bbox")
                    and not block.get("excluded_reason"))
        margins[page["number"]] = xs[len(xs) // 5] if xs else None
    return margins


def judge_boundary(section):
    """结束边界只有显式结构标记才算确认；仅凭版面或文末截断的需核对原页。"""
    reasons = []
    if (section["end_boundary"] == "document_end"
            and not section["content"].rstrip().endswith(tuple("。！？.!?》）)"))):
        reasons.append("文档末尾没有明确正文结束标记")
    if section["end_boundary"] == "layout":
        reasons.append("仅依据版面分段，未见明确的下一条结构标记")
    if any(b - a > 1 for a, b in zip(section["pages"], section["pages"][1:])):
        reasons.append("正文跨越未纳入的页面，需核对原页")
    section["boundary_status"] = "uncertain" if reasons else "confirmed"
    section["boundary_reasons"] = reasons


def assemble_document(pages, title):
    blocks, removed = exclude_blocks(pages)
    margins = left_margins(pages)
    sections, current = [], None

    def finish(boundary):
        if current is not None:
            current["end_boundary"] = boundary

    def begin(label, kind, boundary="explicit_next_unit"):
        nonlocal current
        finish(boundary)
        current = {"id": len(sections) + 1, "title": label, "kind": kind, "content": "",
                   "source_spans": [], "end_boundary": "unknown",
                   "start_boundary": "explicit" if kind in EXPLICIT_START else "layout"}
        sections.append(current)

    def append(block, separator=""):
        text = block["text"]
        start = len(current["content"])
        current["content"] += separator + text
        current["source_spans"].append({
            "start": start, "end": len(current["content"]), "page": block["page"],
            "block_id": block["id"], "method": block["method"],
            "confidence": block["confidence"],
            "boxes": [None] * len(separator) + block.get("boxes", [None] * len(text))})

    for block in blocks:
        text = block["text"]
        is_title = bool(title) and "".join(text.split()) == "".join(title.split())
        if block["kind"] == "table":
            begin(f"表格（PDF 第 {block['page']} 页）", "table")
            current["table_data"] = block["table_data"]
            append(block)
            finish("table_grid")
            current = None
        elif is_title or block["kind"] == "document_title":
            if current is None or current["kind"] != "document_title":
                begin(title or text, "document_title")
            append(block)
        elif heading_level(text) is not None:
            begin(text, "heading")
            append(block)
        elif label := article_label(text):
            begin(label, "article")
            append(block)
        elif EFFECT_START.search(text):
            begin("效力与执行规定", "effect")
            append(block)
        elif re.match(r"^附件\s*[:：]", text):
            begin("附件目录", "text")
            append(block)
        else:
            left = margins[block["page"]]
            indented = (left is not None and block.get("bbox")
                        and block["bbox"][0] - left > block.get("font_size", 12) * 1.3)
            if current is None or current["kind"] in ("heading", "document_title") or (
                    current["kind"] == "text" and current["content"].endswith(SENTENCE_END)
                    and (indented or current["source_spans"][-1]["page"] != block["page"])):
                begin("说明 / 段落", "text", "paragraph_indent" if indented else "layout")
            previous = current["content"]
            separator = ""
            if previous:
                if previous[-1] in "。！？；?" or re.match(r"^[（(][一二三四五六七八九十\d]+[）)]", text):
                    separator = "\n\n"
                elif previous[-1].isascii() and text[0].isascii():
                    separator = " "
            append(block, separator)
    finish("document_end")
    for section in sections:
        section["pages"] = sorted({span["page"] for span in section["source_spans"]})
        judge_boundary(section)
    return {"sections": sections, "pages": pages, "removed_lines": removed,
            "document_title": title}
