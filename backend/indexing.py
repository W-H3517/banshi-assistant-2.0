"""文字和扫描 PDF 共用的结构上下文、实际检索文本及高亮映射。"""

from pathlib import Path

from .pdf import InvalidPDF, extract_pages
from .pdf_structure import article_label, convert_pdf, heading_level, region, text_regions
from .pdf_tables import describe_tables

INDEX_VERSION = "document-context-v1"


def assign_context(sections, title):
    headings, article, article_pages = {}, "", []
    formal_article = ""
    for section in sections:
        content = section["content"].strip()
        section["pure_heading"] = False
        if "".join(content.split()) == "".join(title.split()):
            section["pure_heading"] = True
            section["kind"] = "document_title"
        elif section["kind"] != "table" and (level := heading_level(content)) is not None:
            headings = {key: value for key, value in headings.items() if key < level}
            headings[level] = (content, section["pages"])
            if level >= 3 and formal_article:
                article = formal_article
            else:
                formal_article, article, article_pages = "", "", []
            section["pure_heading"] = True
        elif section["kind"] not in ("table", "review") and (label := article_label(content)):
            if label.startswith("第"):
                formal_article = label
                headings = {key: value for key, value in headings.items() if key < 3}
            article = f"{formal_article} / {label}" if formal_article and label != formal_article else label
            article_pages = [section["pages"][0]]
            section["pure_heading"] = content == label
        if section["kind"] == "review" or content.startswith(("附件：", "附件:")):
            formal_article, article, article_pages = "", "", []
            headings = {}
        elif not article.startswith("第") and content.startswith(("本通知", "特此通知")):
            headings, article, article_pages = {}, "", []
        section["chapter"] = " / ".join(value[0] for _, value in sorted(headings.items()))
        section["article"] = article
        section["context_pages"] = sorted({p for _, pages in headings.values() for p in pages}
                                          | set(article_pages))


def prepare_document(raw, filename, embeddings, ocr):
    scan_pages = []
    texts = extract_pages(raw, scan_pages=scan_pages)
    if scan_pages:
        parsed = ocr.parse(raw, scan_pages)
        title = parsed["sections"][0]["content"][:160]
    else:
        title = next(text for text in texts if text).splitlines()[0][:160]
        parsed = convert_pdf(raw, title)
    # 单行的完整规定是正文，不把它当作可丢弃的文件标题。
    if title.endswith(("。", "！", "？")):
        title = Path(filename).stem
    assign_context(parsed["sections"], title)
    preview = build_chunks(parsed, title, filename, embeddings)
    if not preview["chunks"]:
        raise InvalidPDF("PDF 中只有标题或空白内容，没有可建立索引的正文")
    return title, {"version": INDEX_VERSION, "parsed": parsed, "preview": preview}


def build_chunks(parsed, title, filename, embeddings):
    tables = describe_tables(parsed["sections"], title)
    chunks = []
    ocr_pages = [p["number"] for p in parsed["pages"] if p.get("ocr")]
    for section in parsed["sections"]:
        if section["pure_heading"]:
            continue
        source = {"document_title": title, "filename": filename,
                  "chapter": section["chapter"], "article": section["article"]}
        context = f"文件：{title}\n文件名：{filename}\n"
        if source["chapter"]:
            context += f"章节：{source['chapter']}\n"
        if source["article"]:
            context += f"条目：{source['article']}\n"
        context += "正文：\n"
        table = next((t for t in tables if t["section_id"] == section["id"]), None)
        units = table["rows"] if table else [{"content": section["content"]}]
        for unit in units:
            parts = embeddings.split(context, unit["content"])
            for part in parts:
                start, end = part["start"], part["end"]
                warnings = []
                if table:
                    boxes = list(dict.fromkeys(tuple(field["bbox"]) for field in unit["fields"]
                                               if field["bbox"] is not None))
                    regions = [r for box in boxes
                               if (r := region(box, parsed["pages"][unit["page"] - 1]))]
                    status = "complete" if regions and len(regions) == len(boxes) else "partial" if regions else "unlocated"
                    pages = [unit["page"]]
                    warnings.append("表格字段、单位和续页表头关系待核对")
                else:
                    spans = [s for s in section["source_spans"] if s["start"] < end and s["end"] > start]
                    pages = sorted({s["page"] for s in spans})
                    regions, status = text_regions(section, start, end, parsed["pages"])
                    if section["kind"] == "review":
                        warnings.append("落款或印章区域，识别文字及归属需核对")
                    if any(s.get("confidence") is not None and s["confidence"] < .9 for s in spans):
                        warnings.append("含低分识别文字，请对照原文")
                if len(parts) > 1:
                    warnings.append("原段落或表格行超过输入预算，已拆分并继承相同所属信息")
                retrieval_text = context + part["content"]
                chunk = {"ordinal": len(chunks), "section_id": section["id"],
                         "source": source, "context_pages": section["context_pages"],
                         "title": source["article"] or (table["title"] if table else "正文段落"),
                         "chapter": source["chapter"], "kind": "table_row" if table else section["kind"],
                         "pages": pages, "content": part["content"], "retrieval_text": retrieval_text,
                         "start": start, "end": end,
                         "token_count": len(embeddings.tokenizer.encode(retrieval_text).ids),
                         "regions": regions, "location_status": status,
                         "needs_review": bool(warnings), "warnings": warnings}
                if table:
                    chunk.update(row=unit["row"], header_pages=table["header_pages"])
                chunks.append(chunk)
    return {"version": INDEX_VERSION,
            "pages": [{k: v for k, v in p.items() if k != "lines"} for p in parsed["pages"]],
            "chunks": chunks, "removed_line_count": len(parsed.get("removed_lines", [])),
            "ocr_pages": ocr_pages,
            "warnings": (["扫描识别的日期、数字和印章可能有误；高分不代表正确。",
                          "扫描页按单栏恢复，表格和多栏需核对原文。"] if ocr_pages else [])}
