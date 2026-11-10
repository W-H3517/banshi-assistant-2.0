"""本地扫描 PDF 识别、单栏段落恢复及来源坐标；请求期间不下载模型。"""

import hashlib
import logging
import threading
from pathlib import Path
from urllib.request import urlopen

import pypdfium2 as pdfium
from rapidocr import RapidOCR

from .pdf import InvalidPDF

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

    def extract_page(self, raw, number, view):
        """仅识别指定扫描页，输出与文字页共用的绝对坐标行；不参与文档组装。"""
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
            sx, sy = (view[2] - view[0]) / image.width, (view[3] - view[1]) / image.height
            for line in lines:
                line["polygon"] = [[x * sx + view[0], y * sy + view[1]] for x, y in line["polygon"]]
                words = line.pop("words")
                line["boxes"] = ([bbox([[x * sx + view[0], y * sy + view[1]] for x, y in word[2]])
                                  for word in words for _ in word[0]]
                                 if "".join(w[0] for w in words) == line["text"]
                                 else [None] * len(line["text"]))
        finally:
            image.close()
        if not lines:
            raise InvalidPDF(f"第 {number} 页未识别到文字，请核对扫描质量或移除空白扫描页")
        logger.info("扫描页识别完成 page=%d lines=%d", number, len(lines))
        return lines
