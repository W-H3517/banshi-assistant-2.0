"""查表：在一张逻辑表内按单元格值筛行，返回结构化字段、单位与每行引用。

表格问题多为查数（某地区、某类别的标准），按单元格筛选比向量相似度可靠。筛选按表内稀有度：
在一成以上（且多于 3 行）的行里都出现的词（如“全市”“其他地区”）区分不了行，不参与匹配；越少见的词权重越高。
搜索命中大表时用同一规则，把与查询相关的几行直接附在结果里，避免同表无关行占用名额。
"""

import math
import re

from sqlalchemy import select

from ..db import DocumentIndex
from ..indexing.table_text import (
    UNIT_UNKNOWN,
    field_names,
    grouped_fields,
    row_key,
    row_retrieval_text,
    unit_suffix,
)
from .outline import find_outline

MAX_ROWS = 20         # 查表最多返回的行数
SEARCH_ROWS = 3       # 搜索结果里每张大表最多附带的行数
CARD_SAMPLE = 120     # 未筛到行时，信息卡举例的行标识字数
MAX_KEYWORDS = 6
COMMON_SHARE = .1     # 出现在超过这个比例（且多于 3 行）的词视为表内常见词，不参与筛行
NOT_A_TABLE = "该节点不是表格，请使用搜索结果中 kind 为 table 或 table_card 的 node_id"


def squash(text):
    return re.sub(r"\s+", "", text).casefold()


def match_rows(table, words, limit):
    """按表内稀有度筛行：返回 (命中的 [(行, 字段)], 参与匹配的词, 忽略的常见词)。

    每个词的权重为 log(1 + 行数/出现行数)。一行的得分是它包含的词的权重之和，同一行里
    被更长命中词包含的短词（如“技术人员”里的“技术”）不重复计分；同分时出现次数多的在前。
    只保留得分不低于最高分六成的行；没有任何词命中时返回空列表。
    """
    entries = [(row, fields, squash("".join(f["value"] for f in fields)))
               for row in table["rows"] for fields in [grouped_fields(table, row)]]
    total = len(entries)
    counts = {word: sum(word in text for *_, text in entries) for word in dict.fromkeys(words) if word}
    common = [w for w, n in counts.items() if n > max(3, total * COMMON_SHARE)]
    used = [w for w, n in counts.items() if n and w not in common]
    scored = []
    for index, (row, fields, text) in enumerate(entries):
        present = [w for w in used if w in text]
        present = [w for w in present if not any(w != o and w in o for o in present)]
        score = sum(math.log(1 + total / counts[w]) for w in present)
        if score:
            scored.append((score, sum(text.count(w) for w in present), index, row, fields))
    best = max((item[0] for item in scored), default=0)
    ranked = sorted((item for item in scored if item[0] >= best * .6),
                    key=lambda item: (-item[0], -item[1], item[2]))
    return [(row, fields) for *_, row, fields in ranked[:limit]], used, common


def render_table_hit(hit):
    """大表命中的正文：表名、单位加按查询筛出的行；没有筛到行时给列名和部分行标识，便于查表。"""
    rows = hit["table_rows"]
    if not rows:
        return hit["card"] + "\n未按查询词筛到具体行；需要具体数值时用 query_table 按表中写法查询。"
    head = hit["card_head"]
    lines = [f"按查询筛出的行（全表 {hit['total_rows']} 行）："]
    return "\n".join([head, *lines, *(row["text"] for row in rows)])


class Tables:
    async def attach_table_rows(self, hits, word_lists):
        """为命中大表的结果附上按查询词筛出的行，并改写正文、页码与引用。

        word_lists 为按优先顺序排列的几组查询词，用第一组能筛到行的。"""
        targets = [hit for hit in hits if hit.get("big_table")]
        if not targets:
            return
        async with self.db.sessions() as session:
            loaded = dict((await session.execute(select(
                DocumentIndex.document_id, DocumentIndex.tables).where(
                DocumentIndex.document_id.in_({hit["id"] for hit in targets})))).all())
        for hit in targets:
            table = next(t for t in loaded[hit["id"]] if t["id"] == hit["table_id"])
            matched = []
            for words in word_lists:  # 依次尝试，取第一组能筛到行的词
                matched = match_rows(table, words, SEARCH_ROWS)[0]
                if matched:
                    break
            head = f"表格：{hit['label']}{unit_suffix(table)}"
            keys = "、".join(dict.fromkeys(k for row in table["rows"] if (k := row_key(table, row))))
            hit.update(card="\n".join([
                           head, "列：" + "、".join(n.replace(" / ", "") for n in field_names(table)),
                           f"行（共 {len(table['rows'])} 行），例如：{keys[:CARD_SAMPLE]}……"]),
                       card_head=head, row_keys=keys,
                       # 判断不同文件是否收录同一张表：单位与列名也须一致
                       signature=(table["unit"] or "") + "|" + "|".join(field_names(table)),
                       total_rows=len(table["rows"]),
                       table_rows=[{"row": row["row"], "page": row["page"],
                                    "text": row_retrieval_text(table, row)} for row, _ in matched])
            if matched:
                hit["pages"] = sorted({row["page"] for row, _ in matched})
                hit["page"] = hit["pages"][0]
                hit["citation"] = f"/api/documents/{hit['id']}#page={hit['page']}"
            hit["content"] = render_table_hit(hit)

    async def query_table(self, table_id, keywords=(), columns=()):
        """table_id 为表格（或表格行）的节点 ID；keywords 匹配单元格值，columns 只保留相关列。"""
        keywords = [squash(k) for k in keywords if k and k.strip()]
        columns = [squash(c) for c in columns if c and c.strip()]
        if len(keywords) > MAX_KEYWORDS or len(columns) > MAX_KEYWORDS:
            raise ValueError(f"关键词和列名各最多 {MAX_KEYWORDS} 个")
        outline = await find_outline(self.db, table_id)
        node = outline.node(table_id)
        if "table_id" not in node:
            raise ValueError(NOT_A_TABLE)
        table_node = node if node["kind"] == "table" else outline.by_id[node["parent_id"]]
        async with self.db.sessions() as session:
            tables = await session.scalar(select(DocumentIndex.tables).where(
                DocumentIndex.document_id == outline.document_id))
        table = next(t for t in tables if t["id"] == node["table_id"])
        row_nodes = {n["row"]: n["id"] for n in outline.nodes
                     if n["kind"] == "table_row" and n["parent_id"] == table_node["id"]}

        if keywords:
            matched, used, common = match_rows(table, keywords, MAX_ROWS + 1)
        else:
            matched, used, common = [(row, grouped_fields(table, row)) for row in table["rows"]], [], []

        def keep(index, field):
            return index == 0 or not columns or any(c in squash(field["name"]) for c in columns)

        rows = [{"row": row["row"], "node_id": row_nodes.get(row["row"]), "page": row["page"],
                 "citation": outline.citation(row["page"]),
                 "fields": {f["name"]: f["value"] for i, f in enumerate(fields) if keep(i, f)},
                 **({"inherited": [f["name"] for f in fields if f["inherited"]]}
                    if any(f["inherited"] for f in fields) else {})}
                for row, fields in matched[:MAX_ROWS]]
        result = {
            "id": outline.document_id, "title": outline.title,
            "table": {"node_id": table_node["id"], "label": table_node["label"],
                      "unit": table["unit"] or UNIT_UNKNOWN, "columns": field_names(table),
                      "header_pages": table["header_pages"], "total_rows": len(table["rows"])},
            "rows": rows,
            "warnings": outline.warnings(table_node["pages"]) + table_node["warnings"]}
        if table.get("notes"):
            result["table"]["notes"] = table["notes"]
        if common:
            result["ignored_keywords"] = common  # 表内大多数行都有，区分不了行
        if keywords and not used:
            result["note"] = "关键词在表中都没有出现，或出现在大多数行里；换用表中的写法（如地区全称、类别名）。"
        elif len(matched) > MAX_ROWS:
            result["note"] = f"符合的行超过 {MAX_ROWS} 行，只返回最相关的 {MAX_ROWS} 行；可增加关键词缩小范围。"
        if any("inherited" in r for r in rows):
            result["inherited_note"] = "inherited 中的字段承接上页合并单元格的值，原页该格为空白。"
        return result
