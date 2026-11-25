"""索引构建：解析结果 → 结构树与阅读单元 → 检索片段 → 规则卡。

流水线单向进行，后一步只读取前一步的结果。产出的快照按用途分块，
文字只在阅读单元中保存一份；逐字坐标仅用于计算高亮，不入库。
"""

from pathlib import Path

from ..parsing import InvalidPDF, parse_pdf
from .chunks import build_chunks
from .rules import build_rule_card
from .structure import build_structure

INDEX_VERSION = "table-rows-v8"
OCR_WARNINGS = ["扫描识别的日期、数字和印章可能有误；高分不代表正确。",
                "扫描页按单栏恢复，扫描表格和多栏需核对原文；文字页保留表格网格。"]


def prepare_document(raw, filename, embeddings, ocr):
    parsed = parse_pdf(raw, ocr)
    title = parsed["document_title"] or Path(filename).stem
    structure = build_structure(parsed["sections"], parsed["tables"], title, filename)
    chunks = build_chunks(structure, parsed["tables"], parsed["pages"], title, embeddings)
    if not chunks:
        raise InvalidPDF("PDF 中只有标题或空白内容，没有可建立索引的正文")
    ocr_pages = [page["number"] for page in parsed["pages"] if page["ocr"]]
    return title, {
        "version": INDEX_VERSION,
        "generation": structure["generation"],
        "meta": {"filename": filename, "ocr_pages": ocr_pages,
                 "pages": [{key: page[key] for key in ("number", "width", "height")}
                           for page in parsed["pages"]],
                 "removed_line_count": len(parsed["removed_lines"]),
                 "warnings": OCR_WARNINGS if ocr_pages else []},
        "nodes": structure["nodes"],
        "units": [{**unit, "spans": [{key: span[key] for key in ("start", "end", "page")}
                                     for span in unit["spans"]]} for unit in structure["units"]],
        "tables": parsed["tables"],
        # 原页块供按页核验，包含被排除的页脚、脚注和落款。
        "blocks": [[{key: block[key] for key in ("id", "kind", "text", "excluded_reason")
                     if key in block} for block in page["blocks"]] for page in parsed["pages"]],
        "rules": build_rule_card(structure),
        "chunks": chunks,
    }
