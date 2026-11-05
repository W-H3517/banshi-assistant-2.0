"""文字 PDF 解析；页码始终使用从 1 开始的物理页码。"""

from io import BytesIO

import pdfplumber
from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError
from pdfminer.psparser import PSEOF, PSSyntaxError
from pdfplumber.utils.exceptions import PdfminerException

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_PAGES = 200


class InvalidPDF(ValueError):
    pass


def extract_pages(raw: bytes) -> list[str]:
    if not raw.startswith(b"%PDF-"):
        raise InvalidPDF("文件不是有效的 PDF")
    try:
        with pdfplumber.open(BytesIO(raw)) as pdf:
            if pdf.doc.encryption:
                raise InvalidPDF("暂不支持加密 PDF，请上传解除加密后的文件")
            if not 1 <= len(pdf.pages) <= MAX_PAGES:
                raise InvalidPDF(f"PDF 页数必须在 1 至 {MAX_PAGES} 页之间")
            pages = []
            for number, page in enumerate(pdf.pages, 1):
                text = (page.dedupe_chars().extract_text(x_tolerance=2) or "").strip()
                if page.images and len(text) < 30:
                    raise InvalidPDF(f"第 {number} 页可能是扫描件，本版本仅支持文字 PDF")
                if "(cid:" in text:
                    raise InvalidPDF(f"第 {number} 页文字编码无法可靠提取，请重新导出文字 PDF")
                pages.append(text)
                page.close()
            if not any(pages):
                raise InvalidPDF("PDF 中没有可提取的文字；暂不支持扫描件 OCR")
            if sum(map(len, pages)) > 1_000_000:
                raise InvalidPDF("PDF 文字过多，请按制度拆分后上传")
            return pages
    except (PDFPasswordIncorrect, PDFEncryptionError):
        raise InvalidPDF("暂不支持加密 PDF，请上传解除加密后的文件") from None
    except PdfminerException as exc:
        if isinstance(exc.args[0], (PDFPasswordIncorrect, PDFEncryptionError)):
            raise InvalidPDF("暂不支持加密 PDF，请上传解除加密后的文件") from None
        raise InvalidPDF("PDF 结构损坏或无法解析，请重新导出后上传") from None
    except (PDFSyntaxError, PSEOF, PSSyntaxError, UnicodeError, TypeError, KeyError):
        raise InvalidPDF("PDF 结构损坏或无法解析，请重新导出后上传") from None
