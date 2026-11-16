"""PDF 解析：页面块 → 版面识别 → 连续段落 → 逻辑表。只依赖原文件，不涉及索引。"""

from .layout import classify_layout
from .pages import MAX_FILE_BYTES, InvalidPDF, extract_pages
from .sections import assemble_document
from .tables import build_tables

__all__ = ["MAX_FILE_BYTES", "InvalidPDF", "parse_pdf"]


def parse_pdf(raw, ocr=None):
    pages = extract_pages(raw, ocr)
    parsed = assemble_document(pages, classify_layout(pages))
    parsed["tables"] = build_tables(parsed["sections"])
    return parsed
