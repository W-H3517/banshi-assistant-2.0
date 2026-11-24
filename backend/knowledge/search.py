"""正文混合检索：BM25 与向量两路排名按阅读单元融合，并附带命中文件的规则卡。"""

import asyncio
import logging
import re

import jieba
import numpy as np
from rank_bm25 import BM25Okapi
from sqlalchemy import select

from ..db import Chunk, Document, DocumentIndex
from .outline import STALE, find_outline, load_outlines, normalize_label

logger = logging.getLogger(__name__)
jieba.setLogLevel(logging.WARNING)
STOP_WORDS = {"的", "了", "和", "是", "在", "要", "有", "我", "你", "请", "什么", "哪些", "怎么",
              "如何", "是否", "可以", "需要", "多少", "一个", "这个", "进行", "关于"}
MAX_RESULTS = 6
MAX_SEARCH_QUERIES = 4
BATCH_RESULTS_PER_QUERY = 5
RANK_DEPTH = 20  # 每路排名参与融合的阅读单元数
# 这是候选召回阈值，不是“有制度依据”的判定；由 Agent 核对片段是否支持结论。
MIN_SIMILARITY = 0.40


def terms(text):
    return [word.casefold() for word in jieba.cut_for_search(text)
            if word not in STOP_WORDS and re.search(r"\w", word) and len(word) > 1]


def fingerprint(text):
    """忽略空白与标点的内容指纹，用于识别不同文件里转载的同一段内容。"""
    return re.sub(r"[\W_]+", "", text).casefold()


def compact(hit, multiple):
    """交给模型的命中条目：只保留定位、引用和正文，默认状态不输出。"""
    item = {"id": hit["id"], "node_id": hit["node_id"], "pages": hit["pages"],
            "citation": hit["citation"]}
    if path := " / ".join(filter(None, (hit["source"]["chapter"], hit["source"]["article"]))):
        item["path"] = path
    if hit["article_id"] and hit["article_id"] != hit["node_id"]:
        item["article_id"] = hit["article_id"]
    if hit["kind"] == "table_row":
        item["kind"] = "table_row"
    elif not hit["excerpt_complete"]:
        item["partial"] = True  # 只是所属段落的一部分，可按 node_id 补读
    if hit["boundary_status"] != "confirmed":
        item["boundary_uncertain"] = hit["boundary_reasons"]
    if hit["warnings"]:
        item["warnings"] = hit["warnings"]
    if hit["also_in"]:
        item["also_in"] = hit["also_in"]
    if multiple:
        item["matched_queries"] = hit["matched_queries"]
    item["content"] = hit["content"]
    return item


def match_reference(name, library):
    """按名称匹配被引用的文件，允许省略“（修订）”后缀；不推断版本效力。"""
    wanted = normalize_label(name)
    matched = [document_id for document_id, title, filename in library
               if wanted in (normalize_label(title),
                             normalize_label(re.sub(r"[（(]修订[）)]$", "", title)),
                             normalize_label(filename.removesuffix(".pdf")))]
    return {"title": name, "document_ids": matched,
            "status": "matched" if len(matched) == 1 else "ambiguous" if matched else "not_matched",
            "note": "按名称匹配，允许省略修订后缀，不推断版本效力；未匹配时不能借用其他文件金额"}


class Search:
    async def search(self, query, document_id=None, node_id=None):
        query = query.strip()
        if not query:
            return []
        if len(query) > 300:
            raise ValueError("搜索内容不能超过 300 字，请使用一个具体问题")
        allowed = None
        if node_id:
            scope = await find_outline(self.db, node_id)
            if document_id and scope.document_id != document_id:
                raise ValueError(STALE)
            document_id, parent = scope.document_id, scope.node(node_id)["id"]
            allowed = {node["id"] for node in scope.nodes if scope.within(node, parent)}
        statement = select(Chunk.id, Chunk.document_id, Chunk.node_id, Chunk.content,
                           Chunk.vector).order_by(Chunk.document_id, Chunk.ordinal)
        if document_id:
            statement = statement.where(Chunk.document_id == document_id)
        async with self.db.sessions() as session:
            rows = [row for row in await session.execute(statement)
                    if allowed is None or row.node_id in allowed]
        if not rows:
            return []
        ranked = await asyncio.to_thread(self.rank, query, rows)
        groups, owners, repeats = {}, {}, {}
        for index in ranked:
            row = rows[index]
            if row.node_id in groups:
                groups[row.node_id].append(row)
                continue
            if row.node_id in repeats:
                continue
            # 内容相同的阅读单元（如两份文件收录同一张表）并入先命中的一条，只占一个名额。
            owner = owners.get(mark := fingerprint(row.content))
            if owner:
                repeats[row.node_id] = (owner, row)
            elif len(groups) < MAX_RESULTS:
                groups[row.node_id] = [row]
                owners[mark] = row.node_id
        # 只为最终命中读取片段明细和所属文档的结构。
        shown = [group[0] for group in groups.values()] + [row for _, row in repeats.values()]
        async with self.db.sessions() as session:
            details = dict((await session.execute(select(Chunk.id, Chunk.detail).where(
                Chunk.id.in_([row.id for row in shown])))).all())
        outlines = await load_outlines(self.db, {row.document_id for row in shown})
        also = {}
        for owner, row in repeats.values():
            pages = details[row.id]["pages"]
            also.setdefault(owner, []).append({
                "id": row.document_id, "title": outlines[row.document_id].title,
                "node_id": row.node_id, "pages": pages,
                "citation": outlines[row.document_id].citation(pages[0])})
        hits = []
        for group in groups.values():
            first = group[0]
            outline, detail = outlines[first.document_id], details[first.id]
            node = outline.node(first.node_id)
            article = outline.article(node)
            whole = detail["start"] == 0 and detail["end"] == node["length"]
            hits.append({
                "also_in": also.get(first.node_id, []),
                "id": first.document_id, "title": outline.title, "chunk_id": first.id,
                "node_id": node["id"], "article_id": article["id"] if article else None,
                "matched_chunk_ids": [row.id for row in group], "page": detail["pages"][0],
                "pages": detail["pages"], "content": first.content, "source": detail["source"],
                "kind": node["kind"], "unit_id": node["unit_id"],
                "excerpt_complete": node["kind"] != "table_row" and whole,
                "boundary_status": node["boundary_status"],
                "boundary_reasons": node["boundary_reasons"],
                "citation": outline.citation(detail["pages"][0]),
                "warnings": outline.warnings(detail["pages"]) + detail["warnings"]})
        logger.info("正文检索 candidates=%d groups=%d", len(rows), len(hits))
        return hits

    def rank(self, query, rows):
        for row in rows:
            if row.id not in self.term_cache:
                self.term_cache[row.id] = terms(row.content)
        corpus = [self.term_cache[row.id] for row in rows]
        query_terms = terms(query)
        lexical = BM25Okapi(corpus).get_scores(query_terms) if any(corpus) else np.zeros(len(rows))
        query_vector = self.embeddings.query(query)
        matrix = np.stack([np.frombuffer(row.vector, dtype=np.float32) for row in rows])
        similarities = matrix @ query_vector
        lexical_order = sorted(
            (i for i, words in enumerate(corpus) if set(words).intersection(query_terms)),
            key=lambda i: -lexical[i],
        )
        semantic_order = [int(i) for i in np.argsort(-similarities)
                          if similarities[i] >= MIN_SIMILARITY]
        scores, candidates = {}, set()
        # 每路先限制阅读单元数；同一单元的多个子片段只贡献一次排名。
        for ranking in (lexical_order, semantic_order):
            groups = {}
            for index in ranking:
                key = rows[index].node_id
                if key not in groups:
                    if len(groups) == RANK_DEPTH:
                        continue
                    groups[key] = len(groups) + 1
                    scores[key] = scores.get(key, 0) + 1 / (60 + groups[key])
                candidates.add(index)
        return sorted(candidates,
                      key=lambda i: (-scores[rows[i].node_id], -float(similarities[i]), i))

    async def search_bundle(self, queries, document_id=None, node_id=None, sent_cards=None):
        """Agent 用的检索：最多四个查询轮流合并，返回精简条目和尚未提供过的规则卡。

        sent_cards 是本次对话已提供过规则卡的文件 ID 集合，会就地补入本次新提供的文件。
        """
        if not isinstance(queries, list) or not 1 <= len(queries) <= MAX_SEARCH_QUERIES:
            raise ValueError(f"搜索须提供1至{MAX_SEARCH_QUERIES}个查询")
        if any(not isinstance(q, str) or not q.strip() or len(q) > 300 for q in queries):
            raise ValueError("每个查询须为1至300字")
        queries = [q.strip() for q in queries]
        if len(set(queries)) != len(queries):
            raise ValueError("查询不能重复")
        groups = [await self.search(q, document_id, node_id) for q in queries]
        selected, marks = {}, {}
        # 限制送给模型的条目总数。重复命中让出的名额仍可用来补充其他证据。
        limit = MAX_RESULTS if len(queries) == 1 else BATCH_RESULTS_PER_QUERY * len(queries)
        # 轮流取每个查询的候选，避免一个子问题挤掉其他子问题；内容相同的只保留先取到的一条。
        for rank in range(MAX_RESULTS):
            for hits in groups:
                if rank < len(hits):
                    hit = hits[rank]
                    if marks.setdefault(fingerprint(hit["content"]), hit["node_id"]) == hit["node_id"]:
                        selected.setdefault(hit["node_id"], dict(hit))
        hits = list(selected.values())[:limit]
        for item in hits:
            item["matched_queries"] = [i for i, hits in enumerate(groups)
                                        if any(h["node_id"] == item["node_id"] for h in hits)]
        documents = {hit["id"]: hit["title"] for hit in hits}
        sent_cards = set() if sent_cards is None else sent_cards
        fresh = {key: title for key, title in documents.items() if key not in sent_cards}
        sent_cards.update(fresh)
        result = {
            "documents": documents | {other["id"]: other["title"]
                                      for hit in hits for other in hit["also_in"]},
            "items": [compact(hit, len(queries) > 1) for hit in hits],
            "experiences": await self.search_experiences(queries),
            "rule_cards": await self.rule_cards(fresh),
            "queries": queries,
            "query_results": [{"query": q, "returned": sum(i in h["matched_queries"] for h in hits)}
                              for i, q in enumerate(queries)],
            "note": "命中数不表示依据齐全；规则卡为原文摘录，不保证自动识别所有效力关系。金额仍需核对适用范围与计算规则。"}
        if len(fresh) < len(documents):
            result["rule_cards_sent_before"] = [title for key, title in documents.items()
                                                if key not in fresh]
        return result

    async def rule_cards(self, documents):
        """命中文件各附一张规则卡；外部文件指向按名称在全库中匹配。"""
        if not documents:
            return []
        ids = list(documents)
        async with self.db.sessions() as session:
            library = (await session.execute(
                select(Document.id, Document.title, Document.filename))).all()
            cards = dict((await session.execute(select(
                DocumentIndex.document_id, DocumentIndex.rules,
            ).where(DocumentIndex.document_id.in_(ids)))).all())
        return [{"id": key, "title": title, "note": cards[key]["note"],
                "rules": [{**{field: rule[field] for field in (
                    "kinds", "content", "node_id", "pages", "boundary_status", "scope_path",
                )}, "id": key, "title": title,
                            "citation": f"/api/documents/{key}#page={rule['pages'][0]}",
                            "references": [match_reference(name, library)
                                           for name in rule["references"]]}
                           for rule in cards[key]["rules"]]}
                for key, title in documents.items()]
