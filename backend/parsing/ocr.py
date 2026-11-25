"""本地扫描 PDF 识别、单栏段落恢复及来源坐标；请求期间不下载模型。"""

import hashlib
import logging
import threading
from pathlib import Path
from urllib.request import urlopen

import pypdfium2 as pdfium
from rapidocr import RapidOCR

from .pages import InvalidPDF
from .scan_tables import recognize_tables

logger = logging.getLogger(__name__)
# PDFium 即使处理不同文档也不能并行调用。
PDFIUM_LOCK = threading.Lock()
MODEL_URL = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/"
# 文字识别三件套，以及扫描页表格用的版面检测（中文文档 CDLA）和无框线表格结构（SLANet+）。
MODELS = {
    "Det": (MODEL_URL + "PP-OCRv6/det/PP-OCRv6_det_small.onnx",
            "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f"),
    "Rec": (MODEL_URL + "PP-OCRv6/rec/PP-OCRv6_rec_small.onnx",
            "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"),
    "Cls": (MODEL_URL + "PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_mobile.onnx",
            "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c"),
    "Layout": ("https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/pp_layout/layout_cdla.onnx",
               "25b1f27ec56aa932a48f30cbd6293c358a156280f4b20b0a973bab210c39f62c"),
    "Table": ("https://www.modelscope.cn/models/RapidAI/RapidTable/resolve/v2.0.0/slanet-plus.onnx",
              "d57a942af6a2f57d6a4a0372573c696a2379bf5857c45e2ac69993f3b334514b"),
}


class OCRUnavailable(RuntimeError):
    pass


def model_paths(cache_dir, *, download=False):
    root = Path(cache_dir) / "ocr"
    paths = {}
    for task, (url, digest) in MODELS.items():
        path = root / Path(url).name
        valid = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
        if not valid and download:
            root.mkdir(parents=True, exist_ok=True)
            with urlopen(url, timeout=120) as response:
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


def char_boxes(text, words, sx, sy, view):
    """逐字结果不含整行文字里的空格：跳过空白对齐，空白位置无字框；非空白字对不上时整行无字框。"""
    chars = [(char, bbox([[x * sx + view[0], y * sy + view[1]] for x, y in word[2]]))
             for word in words for char in word[0] if not char.isspace()]
    if "".join(char for char, _ in chars) != "".join(char for char in text if not char.isspace()):
        return [None] * len(text)
    boxes = iter(box for _, box in chars)
    return [None if char.isspace() else next(boxes) for char in text]


class LocalOCR:
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        self.engine = self.layout = self.structure = None
        self.lock = threading.Lock()

    def recognize(self, image):
        with self.lock:
            if self.engine is None:
                paths = model_paths(self.cache_dir)
                self.engine = RapidOCR(params={
                    **{key: paths[key] for key in ("Det.model_path", "Rec.model_path", "Cls.model_path")},
                    "Global.max_side_len": 4000,
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

    def find_tables(self, image, lines, to_pdf):
        """扫描页上的表格（像素坐标的 OCR 行 → PDF 坐标的网格），模型首次使用时加载。"""
        with self.lock:
            if self.layout is None:
                from rapid_layout import ModelType as LayoutType
                from rapid_layout import RapidLayout, RapidLayoutInput
                from rapid_table import ModelType as TableType
                from rapid_table import RapidTable, RapidTableInput
                paths = model_paths(self.cache_dir)
                self.layout = RapidLayout(RapidLayoutInput(
                    model_type=LayoutType.PP_LAYOUT_CDLA, model_dir_or_path=paths["Layout.model_path"]))
                self.structure = RapidTable(RapidTableInput(
                    model_type=TableType.SLANETPLUS, model_dir_or_path=paths["Table.model_path"],
                    use_ocr=False))
            return recognize_tables(image, lines, self.layout, self.structure, to_pdf)

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
            if not lines:
                raise InvalidPDF(f"第 {number} 页未识别到文字，请核对扫描质量或移除空白扫描页")
            sx, sy = (view[2] - view[0]) / image.width, (view[3] - view[1]) / image.height
            for line in lines:  # 先在像素坐标下识别表格，再统一换成 PDF 坐标
                line["boxes"] = char_boxes(line["text"], line.pop("words"), 1, 1, (0, 0))

            def to_pdf(box):
                return (box[0] * sx + view[0], box[1] * sy + view[1],
                        box[2] * sx + view[0], box[3] * sy + view[1])

            tables = self.find_tables(image, lines, to_pdf)
        finally:
            image.close()
        in_tables = {i for table in tables for i in table.pop("line_ids")}
        for line in lines:
            line["polygon"] = [[x * sx + view[0], y * sy + view[1]] for x, y in line["polygon"]]
            line["boxes"] = [None if b is None else list(to_pdf(b)) for b in line["boxes"]]
        lines = [line for i, line in enumerate(lines) if i not in in_tables]
        logger.info("扫描页识别完成 page=%d lines=%d tables=%d", number, len(lines), len(tables))
        return lines, tables
