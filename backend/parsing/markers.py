"""原文显式结构标记：篇章节、条款编号、目录项、页码和效力段起始。"""

import re

NUMBER = r"[零〇一二三四五六七八九十百千两\d]+"
ARTICLE = re.compile(rf"^(第\s*{NUMBER}\s*条)(.*)$", re.DOTALL)
ANNEX = re.compile(r"^附件\s*\d+\s*$")
PAGE_NUMBER = re.compile(r"^(?:[-—–]\s*\d+\s*[-—–]|第\s*\d+\s*页(?:\s*共\s*\d+\s*页)?)$")
TOC_ENTRY = re.compile(r"(?:\.{5,}|…{2,}|·{5,})\s*\d+\s*$")
EFFECT_START = re.compile(
    r"^(?:本(?:通知|办法|规定|细则).*(?:施行|实施|执行)|此前.*(?:规定|办法)|同时废止|以.+为准)")


def heading_level(text):
    """仅识别原文显式层级；带完整句子或规定的编号段落不当纯标题删除。"""
    if TOC_ENTRY.search(text):
        return None
    if re.match(rf"^第\s*{NUMBER}\s*篇", text):
        return 0
    if ANNEX.fullmatch(text) or re.match(rf"^附录\s*{NUMBER}\s*[:：]", text):
        return 0
    if re.match(rf"^第\s*{NUMBER}\s*章", text):
        return 1
    if re.match(rf"^第\s*{NUMBER}\s*节", text):
        return 2
    if len(text) <= 80 and not re.search(r"[。！？；：:]|应当|必须|不得|严禁", text):
        if re.match(r"^[一二三四五六七八九十]+、", text):
            return 3
        if re.match(r"^[（(][一二三四五六七八九十]+[）)]", text):
            return 4
    return None


CHINESE_DIGITS = "零一二三四五六七八九"
# 分点编号，按层级从高到低；层级越小越靠外。
ITEM_MARKERS = (
    re.compile(r"^([一二三四五六七八九十]+)、"),
    re.compile(r"^[（(]([一二三四五六七八九十]+)[）)]"),
    re.compile(r"^(\d+)[.．、](?!\d)"),
    re.compile(r"^[（(](\d+)[）)]"),
    re.compile(r"^([①-⑳])"),
)


def chinese_number(text):
    if "①" <= text <= "⑳":
        return ord(text) - ord("①") + 1
    if text.isdigit():
        return int(text)
    tens, _, ones = text.rpartition("十")
    if "十" not in text:
        return CHINESE_DIGITS.index(text)
    return (CHINESE_DIGITS.index(tens) if tens else 1) * 10 + (CHINESE_DIGITS.index(ones) if ones else 0)


def item_marker(text):
    """行首分点编号：返回 (层级, 序号, 编号原文)，不是分点时返回 None。"""
    for level, pattern in enumerate(ITEM_MARKERS):
        if match := pattern.match(text.lstrip()):
            return level, chinese_number(match[1]), match[0]
    return None


def article_label(text):
    """正式条号取“第…条”；编号问答取到问号，编号短句取到首个句号。"""
    match = ARTICLE.match(text)
    if match:
        return match[1]
    match = re.match(r"^(\d+[.．、])(?=\D)", text)
    if match:
        question = re.match(r"^[^。\n]*?[？?]", text)
        if question:
            return question[0]
        first = text.split("。", 1)[0]
        return first if "。" in text and len(first) <= 50 else match[1]
    return ""
