"""本地扫描 PDF 识别、单栏段落恢复及来源坐标；请求期间不下载模型。"""

import hashlib
import logging
import re
import threading
from io import BytesIO
from pathlib import Path
from statistics import median
from urllib.request import urlopen

import pdfplumber
import pypdfium2 as pdfium
from rapidocr import RapidOCR

from .pdf import InvalidPDF
from .pdf_structure import article_label, heading_level, line_sources

logger = logging.getLogger(__name__)
# PDFium 即使处理不同文档也不能并行调用。
PDFIUM_LOCK = threading.Lock()
MODELS = {
    "Det": ("PP-OCRv6/det/PP-OCRv6_det_small.onnx",
            "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f"),
    "Rec": ("PP-OCRv6/rec/PP-OCRv6_rec_small.onnx",
            "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"),
    "Cls": ("PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
            "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c"),
}
MODEL_URL = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/"


class OCRUnavailable(RuntimeError):
    pass


def model_paths(cache_dir, *, download=False):
    root = Path(cache_dir) / "ocr"
    paths = {}
    for task, (name, digest) in MODELS.items():
        path = root / Path(name).name
        valid = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
        if not valid and download:
            root.mkdir(parents=True, exist_ok=True)
            with urlopen(MODEL_URL + name, timeout=120) as response:
                content = response.read()
            if hashlib.sha256(content).hexdigest() != digest:
                raise OCRUnavailable(f"OCR 模型校验失败：{path.name}")
            temporary = path.with_suffix(".download")
            temporary.write_bytes(content)
            temporary.replace(path)
            valid = True
        if not valid:
            raise OCRUnavailable("OCR 模型未准备或校验失败，请先运行 uv run --locked python -m backend.prepare")
        paths[f"{task}.model_path"] = str(path)
    return paths


def bbox(polygon):
    return [min(p[0] for p in polygon), min(p[1] for p in polygon),
            max(p[0] for p in polygon), max(p[1] for p in polygon)]


class LocalOCR:
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        self.engine = None
        self.lock = threading.Lock()

    def recognize(self, image):
        with self.lock:
            if self.engine is None:
                self.engine = RapidOCR(params={
                    **model_paths(self.cache_dir), "Global.max_side_len": 4000,
                    "Global.log_level": "warning",
                    "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                    "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                })
            result = self.engine(image, return_word_box=True)
            if result.txts is None:
                return []
            return [{"text": text, "confidence": float(score), "polygon": box.tolist(),
                     "words": result.word_results[i] if result.word_results else []}
                    for i, (text, score, box) in enumerate(zip(
                        result.txts, result.scores, result.boxes, strict=True))]

    def parse(self, raw, scan_pages):
        pages = []
        # 渲染后立即释放 PDFium 对象；OCR 期间不持有 PDFium 全局锁。
        with pdfplumber.open(BytesIO(raw)) as native:
            for number, original in enumerate(native.pages, 1):
                if number in scan_pages:
                    with PDFIUM_LOCK, pdfium.PdfDocument(raw) as document:
                        page = document[number - 1]
                        width, height = page.get_size()
                        if width * height * (300 / 72) ** 2 > 40_000_000:
                            raise InvalidPDF(f"第 {number} 页尺寸过大，请缩小页面后上传")
                        bitmap = page.render(scale=300 / 72)
                        image = bitmap.to_pil().convert("RGB")
                        bitmap.close()
                        page.close()
                    try:
                        lines = self.recognize(image)
                        sx, sy = width / image.width, height / image.height
                        for line in lines:
                            line["polygon"] = [[x * sx, y * sy] for x, y in line["polygon"]]
                            words = line.pop("words")
                            line["boxes"] = ([bbox([[x * sx, y * sy] for x, y in word[2]])
                                              for word in words for _ in word[0]]
                                             if "".join(w[0] for w in words) == line["text"]
                                             else [None] * len(line["text"]))
                    finally:
                        image.close()
                    if not lines:
                        raise InvalidPDF(f"第 {number} 页未识别到文字，请核对扫描质量或移除空白扫描页")
                    logger.info("扫描页识别完成 page=%d lines=%d", number, len(lines))
                else:
                    page = original.dedupe_chars()
                    media, crop = page.bbox, page.cropbox or page.bbox
                    x0, y0 = max(media[0], crop[0]), max(media[1], crop[1])
                    width, height = min(media[2], crop[2]) - x0, min(media[3], crop[3]) - y0
                    lines = []
                    for line in page.extract_text_lines(x_tolerance=2):
                        text, boxes = line_sources(line)
                        lines.append({"text": text, "confidence": None,
                                      "polygon": [[line["x0"]-x0, line["top"]-y0],
                                                  [line["x1"]-x0, line["top"]-y0],
                                                  [line["x1"]-x0, line["bottom"]-y0],
                                                  [line["x0"]-x0, line["bottom"]-y0]],
                                      "boxes": [[box[0]-x0, box[1]-y0, box[2]-x0, box[3]-y0]
                                                if box else None for box in boxes]})
                pages.append({"number": number, "width": width, "height": height,
                              "view_box": [0, 0, width, height], "ocr": number in scan_pages,
                              "lines": lines})
                original.close()
        if sum(len(line["text"]) for page in pages for line in page["lines"]) > 1_000_000:
            raise InvalidPDF("PDF 文字过多，请按制度拆分后上传")
        return {"pages": pages, "sections": restore_paragraphs(pages)}


def restore_paragraphs(pages):
    """移植已验证的通知规则；不纠正 OCR 文字，空白页不跨接。"""
    sections, previous = [], None
    for page in pages:
        lines = page["lines"]
        if not lines:
            previous = None
            continue
        boxes = [bbox(line["polygon"]) for line in lines]
        left, right = min(b[0] for b in boxes), max(b[2] for b in boxes)
        height = median(b[3] - b[1] for b in boxes)
        for line, box in zip(lines, boxes, strict=True):
            text = line["text"]
            kind = "text"
            if box[0] > page["width"] * .42:
                kind = "review"
            elif heading_level(text) is not None or not sections:
                kind = "heading"
            new = previous is None or kind != "text" or sections[-1]["kind"] != "text"
            if not new:
                prev_text, prev_box, prev_number, prev_right, prev_height = previous
                if box[0] - left > height * .65 or article_label(text):
                    new = True
                elif prev_number != page["number"]:
                    new = not (prev_box[2] > prev_right - 1.5 * prev_height
                               and not re.search(r"[。！？：:]$", prev_text))
                elif (box[1] - prev_box[3] > height * .9 or
                      (prev_box[2] < right - height * 1.5 and re.search(r"[。！？：:]$", prev_text))):
                    new = True
            if new:
                sections.append({"id": len(sections) + 1, "kind": kind, "title": text[:40],
                                 "chapter": "", "content": "", "source_spans": []})
            section = sections[-1]
            start = len(section["content"])
            section["content"] += text
            section["source_spans"].append({"start": start, "end": len(section["content"]),
                                            "page": page["number"], "boxes": line["boxes"],
                                            "bbox": box, "confidence": line["confidence"]})
            previous = text, box, page["number"], right, height
    for section in sections:
        section["pages"] = sorted({s["page"] for s in section["source_spans"]})
    return sections
