"""PDF 校验与逐页提取：文字页保留表格网格，扫描页交给 OCR，输出统一的页面块。"""

import re
from io import BytesIO
from statistics import median

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError
from pdfminer.psparser import PSEOF, PSSyntaxError
from pdfplumber.utils.exceptions import PdfminerException

from .tables import extract_grid

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PAGES = 200
ENCRYPTED = "暂不支持加密 PDF，请上传解除加密后的文件"
BROKEN = "PDF 结构损坏或无法解析，请重新导出后上传"


class InvalidPDF(ValueError):
    pass


def normalize_line(text):
    # PDF 字距常被提取为中文间的空格；保留英文、数字之间的空格。
    return re.sub(r"(?<=[一-鿿]) +(?=[一-鿿])", "", text.strip())


def line_sources(line):
    """将规范化后的文字逐字关联到原 PDF；不匹配时不猜测坐标。"""
    text = normalize_line(line["text"])
    chars = [(letter, (char["x0"], char["top"], char["x1"], char["bottom"]))
             for char in line["chars"] for letter in char["text"] if not letter.isspace()]
    if "".join(letter for letter, _ in chars) != "".join(text.split()):
        return text, [None] * len(text)
    boxes = iter(box for _, box in chars)
    return text, [None if letter.isspace() else next(boxes) for letter in text]


def native_blocks(page, info):
    """文字页：表格按网格提取，其余文字按行提取并标出页面下部的小字脚注。"""
    view = info["view_box"]
    tables = [t for t in page.find_tables() if len(t.rows) >= 2 and len(t.columns) >= 2]
    table_boxes = [t.bbox for t in tables]

    def outside_tables(obj):
        x = (obj.get("x0", 0) + obj.get("x1", 0)) / 2
        y = (obj.get("top", 0) + obj.get("bottom", 0)) / 2
        return not any(x0 <= x <= x1 and top <= y <= bottom for x0, top, x1, bottom in table_boxes)

    blocks = []
    body_size = median(c["size"] for c in page.chars) if page.chars else 0
    for line in page.filter(outside_tables).extract_text_lines(x_tolerance=2):
        text, boxes = line_sources(line)
        size = median(c["size"] for c in line["chars"])
        footnote = size < body_size * .85 and line["top"] > view[1] + info["height"] * .65
        blocks.append({"kind": "footnote" if footnote else "text", "text": text, "boxes": boxes,
                       "bbox": [line["x0"], line["top"], line["x1"], line["bottom"]],
                       "confidence": None, "method": "native", "font_size": size})
    for table in tables:
        grid = extract_grid(table)
        blocks.append({"kind": "table", "bbox": list(table.bbox), "table_data": grid,
                       "text": "\n".join(" | ".join(cell or "" for cell in row)
                                         for row in table.extract()),
                       "confidence": None, "method": "native"})
    return blocks


def ocr_blocks(ocr, raw, info):
    blocks = []
    for line in ocr.extract_page(raw, info["number"], info["view_box"]):
        polygon = line["polygon"]
        blocks.append({"kind": "text", "text": line["text"], "boxes": line["boxes"],
                       "bbox": [min(p[0] for p in polygon), min(p[1] for p in polygon),
                                max(p[0] for p in polygon), max(p[1] for p in polygon)],
                       "confidence": line["confidence"], "method": "ocr"})
    return blocks


def number_blocks(info):
    """按阅读顺序排序并编号；页面上下各 10% 记为页边，供页眉页脚判断。"""
    top, height = info["view_box"][1], info["height"]
    info["blocks"].sort(key=lambda block: (block["bbox"][1], block["bbox"][0]))
    for index, block in enumerate(info["blocks"]):
        block.update(id=f"p{info['number']}:b{index}", page=info["number"],
                     edge=block["bbox"][1] < top + height * .1
                     or block["bbox"][3] > top + height * .9)


def extract_pages(raw, ocr=None):
    """只打开一次 PDF：校验、逐页判断文字/扫描并提取页面块。"""
    if not raw.startswith(b"%PDF-"):
        raise InvalidPDF("文件不是有效的 PDF")
    pages, characters = [], 0
    try:
        with pdfplumber.open(BytesIO(raw)) as pdf:
            if pdf.doc.encryption:
                raise InvalidPDF(ENCRYPTED)
            if not 1 <= len(pdf.pages) <= MAX_PAGES:
                raise InvalidPDF(f"PDF 页数必须在 1 至 {MAX_PAGES} 页之间")
            for number, original in enumerate(pdf.pages, 1):
                page = original.dedupe_chars()
                text = (page.extract_text(x_tolerance=2) or "").strip()
                if "(cid:" in text:
                    raise InvalidPDF(f"第 {number} 页文字编码无法可靠提取，请重新导出文字 PDF")
                media, crop = page.bbox, page.cropbox or page.bbox
                view = (max(media[0], crop[0]), max(media[1], crop[1]),
                        min(media[2], crop[2]), min(media[3], crop[3]))
                info = {"number": number, "width": view[2] - view[0], "height": view[3] - view[1],
                        "view_box": view, "ocr": bool(page.images) and len(text) < 30, "blocks": []}
                if not info["ocr"]:
                    info["blocks"] = native_blocks(page, info)
                    characters += len(text)
                pages.append(info)
                original.close()
    except (PDFPasswordIncorrect, PDFEncryptionError):
        raise InvalidPDF(ENCRYPTED) from None
    except PdfminerException as exc:
        if isinstance(exc.args[0], (PDFPasswordIncorrect, PDFEncryptionError)):
            raise InvalidPDF(ENCRYPTED) from None
        raise InvalidPDF(BROKEN) from None
    except (PDFSyntaxError, PSEOF, PSSyntaxError, UnicodeError, TypeError, KeyError):
        raise InvalidPDF(BROKEN) from None
    if not characters and not any(page["ocr"] for page in pages):
        raise InvalidPDF("PDF 中没有可提取的文字")
    if characters > 1_000_000:
        raise InvalidPDF("PDF 文字过多，请按制度拆分后上传")
    for info in pages:
        if info["ocr"]:
            if ocr is None:
                raise InvalidPDF(f"第 {info['number']} 页为扫描页，需要使用 OCR 解析")
            info["blocks"] = ocr_blocks(ocr, raw, info)
        number_blocks(info)
    return pages
