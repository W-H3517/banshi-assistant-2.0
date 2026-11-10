"""基于页面来源的标题、尾部落款和独立效力段识别。"""

import re
from statistics import median

EFFECT_START = re.compile(r"^(?:本(?:通知|办法|规定|细则).*(?:施行|实施|执行)|此前.*(?:规定|办法)|同时废止|以.+为准)")
DATE = re.compile(r"(?:19|20)\d{2}\s*年\s*\d{1,2}\s*月")


def classify_layout(pages, title=""):
    first = [b for b in pages[0]["blocks"] if b["kind"] == "text"]
    native_sizes = [b["font_size"] for p in pages for b in p["blocks"] if b.get("font_size")]
    body_size = median(native_sizes) if native_sizes else 0
    candidates = []
    if first and body_size:
        largest = max(b.get("font_size", 0) for b in first)
        if largest > body_size * 1.2:
            candidates = [b for b in first if b.get("font_size", 0) >= largest * .9
                          and not re.search(r"[。！？?]|^第.+[章节条]", b["text"])]
    if candidates:
        title = "".join(b["text"] for b in candidates)
        for b in candidates:
            b["kind"] = "document_title"
        # 无正文编号和句子、且存在封面日期的独立封面，其余文字保留为封面信息。
        cover = (any(DATE.search(b["text"]) for b in first)
                 and not any(re.search(r"[。！？?]|^第.+[章节条]|^\d+[.．、]", b["text"]) for b in first))
        if cover:
            for b in first:
                if b not in candidates:
                    b.update(kind="cover_info", excluded_reason="封面机构、出版信息，原页保留")
    if not title and first:
        title = first[0]["text"] if not re.search(r"[。！？?]$", first[0]["text"]) else ""
    # 只检查文档末尾；右对齐本身不构成落款证据。
    last = pages[-1]
    text_blocks = [b for b in last["blocks"] if b["kind"] == "text"]
    closing = next((i for i, b in enumerate(text_blocks) if re.fullmatch(r"特此通知[。.]?", b["text"].strip())), None)
    if closing is not None:
        for b in text_blocks[closing:]:
            b.update(kind="signature", excluded_reason="文末结束语及落款，原页保留")
    elif any(DATE.search(b["text"]) for b in text_blocks[-3:]):
        for b in reversed(text_blocks[-3:]):
            box = b.get("bbox")
            if not box or box[0] < last["view_box"][0] + last["width"] * .5 or len(b["text"]) > 50:
                break
            if DATE.search(b["text"]) or re.search(r"(?:处|局|院|部|委员会)$", b["text"].strip()):
                b.update(kind="signature", excluded_reason="文末右侧署名/日期，原页保留")
            else:
                break
    return title
