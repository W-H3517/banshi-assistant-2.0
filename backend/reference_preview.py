"""显式引用的双向追查与例外候选；不作制度效力或适用性判断。"""

import re

from .document_preview import NUMBER
from .knowledge import terms

REFERENCE = re.compile(rf"第\s*({NUMBER})\s*条|附件\s*({NUMBER})")
BOOK = re.compile(r"《([^《》]+)》")
EXCEPTION = re.compile(r"不适用|除外|但[是，、：:]?|不得|不予|另有规定|特殊|例外|废止|替代|"
                       r"无法|无需|不再|不发放|不承担|承担|提供住宿|超出|超标准")


def normalize(text):
    return re.sub(r"\s+", "", text)


def number_key(text):
    text = normalize(text)
    if text.isdigit():
        return str(int(text))
    digits = dict(zip("零〇一二三四五六七八九两", [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 2]))
    total, current = 0, 0
    for char in text:
        if char in digits:
            current = digits[char]
        else:
            total += (current or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            current = 0
    return str(total + current)


def section_key(section):
    match = REFERENCE.fullmatch(normalize(section["title"]))
    if match:
        return ("article" if match[1] else "annex", number_key(match[1] or match[2]))
    return None


def endpoint(document, section=None):
    included = [section] if section else []
    if section and section["kind"] == "heading":
        following = document["sections"][document["sections"].index(section) + 1:]
        for child in following:
            if child["kind"] == "heading":
                break
            included.append(child)
    return {"document_id": document["id"], "document_title": document["title"],
            "page_count": document.get("page_count", max(s["pages"][-1] for s in document["sections"])),
            "section_id": section["id"] if section else None,
            "section_title": section["title"] if section else "整份制度",
            "pages": sorted({p for s in included for p in s["pages"]}) if section else [1],
            "content": "\n\n".join(s["content"] for s in included) if section
            else "仅明确引用制度名称，尚未定位具体条款。"}


def extract_edges(documents):
    titles = {}
    for document in documents:
        titles.setdefault(normalize(document["title"]), []).append(document)
    edges = []
    for document in documents:
        # 只采纳原文明示的简称，不按相似名称擅自合并制度和版本。
        aliases = {}
        all_text = "\n".join(s["content"] for s in document["sections"])
        for name, alias in re.findall(r"《([^《》]+)》[（(]以下简称[“\"「]?([^”\"」）)]+)", all_text):
            aliases[alias] = name
        for section in document["sections"]:
            if section["kind"] not in {"article", "text"}:
                continue
            text = section["content"]
            books = list(BOOK.finditer(text))
            consumed_books = set()
            for match in REFERENCE.finditer(text):
                if any(b.start() <= match.start() < b.end() for b in books):
                    continue
                if match.start() == 0 and section_key(section):
                    continue  # 条款自身编号不是引用。
                left = text[max(0, match.start() - 120):match.start()]
                sentence = re.split(r"[。！？；\n]", left)[-1]
                names = list(BOOK.finditer(sentence))
                # 同一句可先引用外部制度、再切回本办法；按最近的范围说明归属。
                scopes = [(name.start(), name[1]) for name in names]
                scopes.extend((m.start(), None) for m in re.finditer(
                    r"本(?:办法|规定|制度|细则)", sentence))
                scopes.extend((m.start(), "") for m in re.finditer(
                    r"(?:上述|该|前述)(?:办法|规定|制度|细则)", sentence))
                for alias, name in aliases.items():
                    scopes.extend((m.start(), name) for m in re.finditer(re.escape(alias), sentence)
                                  if not any(b.start() <= m.start() < b.end() for b in names))
                scope = max(scopes, key=lambda item: item[0])[1] if scopes else None
                title = scope or None
                # 未明确定义的外部制度简称，不回退到当前制度的同号条款。
                suffix = re.search(r"([\u4e00-\u9fff]+(?:办法|规定|制度|细则))(?:的)?\s*$", sentence)
                unknown_name = suffix is not None and not any(
                    suffix[1].endswith(name) for name in [
                        "本办法", "本规定", "本制度", "本细则", *aliases,
                    ])
                if names and title:
                    nearest = next((b for b in reversed(books) if b.end() <= match.start()), None)
                    if nearest:
                        consumed_books.add(nearest.start())
                candidates = titles.get(normalize(title), []) if title else [document]
                if scope == "" or unknown_name:
                    candidates = []
                key = ("article" if match[1] else "annex", number_key(match[1] or match[2]))
                targets = [endpoint(doc, s) for doc in candidates for s in doc["sections"]
                           if section_key(s) == key]
                status = "resolved" if len(candidates) == 1 and len(targets) == 1 else (
                    "ambiguous" if len(candidates) > 1 or len(targets) > 1 else "missing")
                edges.append({"source": endpoint(document, section), "targets": targets,
                              "reference": (f"《{title}》" if title else "") + match[0],
                              "status": status, "scope": "section",
                              "reason": "按制度身份和条号匹配；尚未审核版本效力与适用条件。" if status == "resolved"
                              else "目标未入库、条号未识别或制度/版本存在歧义，未自动选择。"})
            for book in books:
                if book.start() in consumed_books:
                    continue
                candidates = titles.get(normalize(book[1]), [])
                edges.append({"source": endpoint(document, section),
                              "targets": [endpoint(doc) for doc in candidates],
                              "reference": book[0], "scope": "document",
                              "status": "resolved" if len(candidates) == 1 else (
                                  "ambiguous" if candidates else "missing"),
                              "reason": "仅制度级引用，不能据此认定涉及某一条；同名多份时不选择版本。"})
    return edges


def trace_references(documents, document_id, section_id):
    document = next(d for d in documents if d["id"] == document_id)
    section = next((s for s in document["sections"] if s["id"] == section_id), None)
    if section is None:
        raise ValueError("条款编号不存在，请刷新转换预览")
    edges = extract_edges(documents)
    outgoing = [e for e in edges if e["source"]["document_id"] == document_id
                and e["source"]["section_id"] == section_id]
    incoming = [e for e in edges if any(t["document_id"] == document_id
                and t["section_id"] in (None, section_id) for t in e["targets"])
                and not (e["source"]["document_id"] == document_id
                         and e["source"]["section_id"] == section_id)]
    linked = {(e["source"]["document_id"], e["source"]["section_id"]) for e in incoming}
    linked.update((t["document_id"], t["section_id"]) for e in outgoing for t in e["targets"])
    generic = {"规定", "办法", "制度", "应当", "按照", "人员", "相关", "执行", "不得", "报销",
               "出差", "凭据", "原则", "原则上", "按规定", "附件", "不予"}

    def topic_terms(text):
        return set(terms(REFERENCE.sub("", text))) - generic

    main_terms = topic_terms(section["content"])
    candidates = []
    for doc in documents:
        for other in doc["sections"]:
            identity = (doc["id"], other["id"])
            if identity == (document_id, section_id) or identity in linked or other["kind"] != "article":
                continue
            overlap = main_terms.intersection(topic_terms(other["content"]))
            cues = list(dict.fromkeys(EXCEPTION.findall(other["content"])))
            if len(overlap) >= 2 and cues:
                same_chapter = (doc["id"] == document_id and bool(section.get("chapter"))
                                and other.get("chapter") == section["chapter"])
                candidates.append({"target": endpoint(doc, other), "shared_terms": sorted(overlap),
                                   "same_chapter": same_chapter,
                                   "cues": cues, "reason": "主题词重合且含限制性措辞；不是明确引用，也未判定为适用例外。"})
    candidates.sort(key=lambda item: (-item["same_chapter"], -len(item["shared_terms"]),
                                    item["target"]["document_id"], item["target"]["section_id"]))
    return {"main": endpoint(document, section), "outgoing": outgoing, "incoming": incoming,
            "exception_candidates": candidates[:10], "candidate_count": len(candidates),
            "document_count": len(documents), "active_for_search": False,
            "notice": "仅追查已入库制度的一跳显式引用；可选目标条款继续追查。简称仅采用原文明确定义。"
            "例外候选使用主题词与限制性措辞，可能漏掉隐含关系；版本效力及适用性需人工核对。"}
