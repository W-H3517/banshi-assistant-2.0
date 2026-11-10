import asyncio
import hashlib
import logging
import re

import jieba
import numpy as np
from rank_bm25 import BM25Okapi
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from .config import ROOT
from .db import Chunk, Conversation, Document, DocumentParse, SeedImport, User
from .embeddings import LocalEmbeddings
from .indexing import INDEX_VERSION, prepare_document
from .ocr import LocalOCR
from .structure import (
    LOCATE_LIMIT,
    READ_BUDGET,
    ancestors,
    decode_cursor,
    encode_cursor,
    get_node,
    node_info,
    normalize_label,
    read_nodes,
    reading_content,
    within,
)

logger = logging.getLogger(__name__)
jieba.setLogLevel(logging.WARNING)
STOP_WORDS = {"的", "了", "和", "是", "在", "要", "有", "我", "你", "请", "什么", "哪些", "怎么",
              "如何", "是否", "可以", "需要", "多少", "一个", "这个", "进行", "关于"}
MAX_RESULTS = 6
# 这是候选召回阈值，不是“有制度依据”的判定；由 Agent 核对片段是否支持结论。
MIN_SIMILARITY = 0.40


class DuplicateDocument(ValueError):
    pass


def terms(text):
    return [word.casefold() for word in jieba.cut_for_search(text)
            if word not in STOP_WORDS and re.search(r"\w", word) and len(word) > 1]


def public_document(doc):
    return {key: getattr(doc, key) for key in (
        "id", "title", "filename", "created_at", "preview", "page_count", "chunk_count",
    )}


class KnowledgeBase:
    def __init__(self, db, embeddings, ocr=None):
        self.db = db
        self.embeddings = embeddings
        self.upload_lock = asyncio.Lock()
        self.ocr = ocr

    @classmethod
    async def create(cls, db, settings):
        logger.info("加载本地中文嵌入模型")
        embeddings = await asyncio.to_thread(LocalEmbeddings, settings.model_cache_dir)
        return cls(db, embeddings, LocalOCR(settings.model_cache_dir))

    async def seed(self):
        async with self.db.sessions() as session:
            admin = await session.scalar(select(User).where(User.username == "admin"))
            existing = set(await session.scalars(select(Document.sha256)))
            imported = set(await session.scalars(select(SeedImport.sha256)))
        for path in sorted((ROOT / "data/fixtures").glob("*.pdf")):
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest in imported:
                continue
            if digest not in existing:
                await self.upload(path.name, raw, admin.id)
            async with self.db.sessions() as session:
                session.add(SeedImport(sha256=digest))
                await session.commit()

    async def delete_document(self, document_id):
        async with self.upload_lock, self.db.sessions() as session:
            if await session.get(Document, document_id) is None:
                return False
            await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
            await session.execute(delete(DocumentParse).where(DocumentParse.document_id == document_id))
            await session.execute(delete(Document).where(Document.id == document_id))
            await session.commit()
        return True

    async def prepare(self, filename, raw):
        title, snapshot = await asyncio.to_thread(
            prepare_document, raw, filename, self.embeddings, self.ocr,
        )
        vectors = await asyncio.to_thread(self.embeddings.passages, snapshot["preview"]["chunks"])
        return title, snapshot, vectors

    @staticmethod
    def store_index(session, document, snapshot, vectors):
        chunks = snapshot["preview"]["chunks"]
        document.chunk_count = len(chunks)
        session.add(DocumentParse(document_id=document.id, payload=snapshot))
        session.add_all([Chunk(document_id=document.id, page=chunk["pages"][0],
                               ordinal=chunk["ordinal"], content=chunk["retrieval_text"],
                               vector=vector.tobytes())
                         for chunk, vector in zip(chunks, vectors, strict=True)])

    async def check_index(self):
        async with self.db.sessions() as session:
            documents = list(await session.scalars(select(Document.id)))
            snapshots = {p.document_id: p.payload for p in await session.scalars(select(DocumentParse))}
        if any(snapshots.get(key, {}).get("version") != INDEX_VERSION for key in documents):
            raise ValueError("发现旧索引；本版本不兼容或自动迁移旧数据，请按开发约定处理旧运行库")

    async def reset_demo(self, user_id):
        async with self.upload_lock:
            prepared = []
            for path in sorted((ROOT / "data/fixtures").glob("*.pdf")):
                raw = path.read_bytes()
                title, snapshot, vectors = await self.prepare(path.name, raw)
                prepared.append((Document(
                    title=title, filename=path.name, sha256=hashlib.sha256(raw).hexdigest(),
                    page_count=len(snapshot["preview"]["pages"]), chunk_count=0,
                    preview=snapshot["preview"]["chunks"][0]["content"][:160],
                    original=raw, uploaded_by=user_id,
                ), snapshot, vectors))
            if len(prepared) != 3:
                raise ValueError("初始模拟制度应为三份，重置已取消")
            async with self.db.sessions.begin() as session:
                for model in (Conversation, Chunk, DocumentParse, Document, SeedImport):
                    await session.execute(delete(model))
                for document, snapshot, vectors in prepared:
                    session.add(document)
                    await session.flush()
                    self.store_index(session, document, snapshot, vectors)
                    session.add(SeedImport(sha256=document.sha256))
        return {"ok": True, "document_count": len(prepared)}

    async def upload(self, filename, raw, user_id):
        digest = hashlib.sha256(raw).hexdigest()
        async with self.upload_lock:
            async with self.db.sessions() as session:
                if await session.scalar(select(Document.id).where(Document.sha256 == digest)):
                    raise DuplicateDocument("该 PDF 已在知识库中，无需重复上传")
            title, snapshot, vectors = await self.prepare(filename, raw)
            document = Document(
                title=title, filename=filename, sha256=digest,
                page_count=len(snapshot["preview"]["pages"]), chunk_count=0,
                preview=snapshot["preview"]["chunks"][0]["content"][:160],
                original=raw, uploaded_by=user_id,
            )
            async with self.db.sessions() as session:
                session.add(document)
                try:
                    await session.flush()
                    self.store_index(session, document, snapshot, vectors)
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    raise DuplicateDocument("该 PDF 已在知识库中，无需重复上传") from None
            logger.info("PDF 索引完成 document_id=%s pages=%d chunks=%d",
                        document.id, document.page_count, document.chunk_count)
            return public_document(document)

    async def documents(self, query=""):
        if query.strip():
            hits = await self.search(query)
            async with self.db.sessions() as session:
                all_docs = list(await session.scalars(select(Document).order_by(Document.created_at.desc())))
            label = normalize_label(query)
            named = [doc.id for doc in all_docs
                     if label in normalize_label(doc.title) or label in normalize_label(doc.filename)]
            ids = list(dict.fromkeys(named + [hit["id"] for hit in hits]))
            docs = {doc.id: doc for doc in all_docs}
            result = []
            for key in ids:
                hit = next((h for h in hits if h["id"] == key), None)
                result.append({**public_document(docs[key]),
                               "preview": hit["content"][:160] if hit else docs[key].preview,
                               "match_page": hit["page"] if hit else 1})
            return result
        async with self.db.sessions() as session:
            return [public_document(doc) for doc in await session.scalars(
                select(Document).order_by(Document.created_at.desc()),
            )]

    async def snapshots(self, document_id=None):
        statement = select(DocumentParse, Document.title).join(Document).order_by(Document.id)
        if document_id:
            statement = statement.where(Document.id == document_id)
        async with self.db.sessions() as session:
            return {parsed.document_id: (parsed.payload, title)
                    for parsed, title in (await session.execute(statement)).all()}

    @staticmethod
    def resolve_node(snapshots, node_id):
        for document_id, (snapshot, title) in snapshots.items():
            if node_id.startswith(snapshot["structure"]["generation"] + ":"):
                unit = next((u for u in snapshot["structure"]["reading_units"] if u["id"] == node_id), None)
                return document_id, snapshot, title, get_node(snapshot["structure"], unit["node_id"] if unit else node_id)
        raise ValueError("节点不存在或索引已更新，请重新搜索或定位")

    async def locate(self, query="", document_id=None, parent_id=None, cursor=None):
        if len(query) > 300:
            raise ValueError("定位内容不能超过 300 字")
        snapshots = await self.snapshots(document_id)
        if document_id and not snapshots:
            raise ValueError("制度不存在，请重新定位")
        if parent_id:
            self.resolve_node(snapshots, parent_id)
        scope = ["locate", query, document_id, parent_id,
                 sorted(s["structure"]["generation"] for s, _ in snapshots.values())]
        offset = decode_cursor(cursor, scope)
        normalized = normalize_label(query)
        tokens = [normalize_label(word) for word in query.split()]
        results = []
        for key, (snapshot, title) in snapshots.items():
            structure = snapshot["structure"]
            for node in structure["nodes"]:
                if parent_id and not within(structure, node, parent_id):
                    continue
                if not query.strip():
                    if parent_id and node["parent_id"] != parent_id:
                        continue
                    if not parent_id and node["kind"] != "document":
                        continue
                else:
                    # 正文不参与结构匹配；有重名时保留全部候选。
                    if node["kind"] not in ("document", "chapter", "article", "table"):
                        continue
                    label = normalize_label(node["label"])
                    if node["kind"] == "document":
                        label += normalize_label(structure["filename"])
                    if normalized not in label and not all(t in label for t in tokens):
                        continue
                results.append(node_info(structure, node, key, title))
        if offset > len(results):
            raise ValueError("定位游标超出范围，请重新定位")
        end = offset + LOCATE_LIMIT
        return {"items": results[offset:end], "complete": end >= len(results),
                "next_cursor": encode_cursor(scope, end) if end < len(results) else None,
                "total": len(results)}

    async def read(self, target_id, scope="node", neighbors=1, cursor=None, page=None):
        if scope not in ("node", "article", "neighbors", "page") or not 0 <= neighbors <= 3:
            raise ValueError("阅读范围须为 node/article/neighbors/page，相邻段数为 0 到 3")
        snapshots = await self.snapshots()
        async with self.db.sessions() as session:
            chunk = await session.get(Chunk, target_id)
        if chunk:
            snapshot, _ = snapshots[chunk.document_id]
            target_id = snapshot["preview"]["chunks"][chunk.ordinal]["node_id"]
        document_id, snapshot, title, target = self.resolve_node(snapshots, target_id)
        structure = snapshot["structure"]
        if scope == "page":
            if page is None or not 1 <= page <= len(snapshot["parsed"]["pages"]):
                raise ValueError("核验原页须指定有效的PDF物理页码")
            nodes = []
            for block in snapshot["parsed"]["pages"][page - 1]["blocks"]:
                nodes.append({**target, "id": f"{target['id']}@{block['id']}",
                              "parent_id": structure["nodes"][0]["id"], "label": f"原页块 {block['id']}",
                              "kind": block["kind"], "content": block["text"], "spans": [],
                              "table": None, "header_pages": [],
                              "source_pages": [page], "pages": [page], "unit_id": None,
                              "boundary_status": "uncertain", "boundary_reasons": ["原页核验不等于整条内容"],
                              "warnings": [block["excluded_reason"]] if block.get("excluded_reason") else []})
        else:
            if page is not None:
                raise ValueError("page参数仅用于原页核验scope=page")
            target, nodes = read_nodes(structure, target, scope, neighbors)
        cursor_scope = ["read", structure["generation"], target_id, scope, neighbors, page]
        offset = decode_cursor(cursor, cursor_scope)
        total = sum(len(n["content"]) for n in nodes)
        if offset > total:
            raise ValueError("续读游标超出内容范围，请重新读取")
        remaining, position, items = READ_BUDGET, 0, []
        for node in nodes:
            text = node["content"]
            start = max(0, offset - position)
            position += len(text)
            if start >= len(text):
                continue
            end = min(len(text), start + remaining)
            pages = sorted({s["page"] for s in node["spans"]
                            if s["start"] < end and s["end"] > start}) or node["source_pages"]
            info = node_info(structure, node, document_id, title)
            items.append({**info, "content": text[start:end], "start": start, "end": end,
                          "pages": pages, "page": pages[0],
                          "citation": f"/api/documents/{document_id}#page={pages[0]}",
                          "excerpt_complete": start == 0 and end == len(text),
                          "unit_id": node["unit_id"], "boundary_status": node["boundary_status"],
                          "boundary_reasons": node["boundary_reasons"], "table": node.get("table"),
                          "header_pages": node.get("header_pages", []),
                          "warnings": snapshot["preview"]["warnings"] + node["warnings"]})
            remaining -= end - start
            if remaining == 0:
                break
        end_offset = offset + READ_BUDGET - remaining
        boundary = "confirmed" if nodes and all(n["boundary_status"] == "confirmed" for n in nodes) else "uncertain"
        reasons = list(dict.fromkeys(r for n in nodes for r in n["boundary_reasons"]))
        if not nodes:
            reasons.append("该结构节点没有可读取正文，需核对原页")
        source_pages = sorted({p for n in nodes for p in n["source_pages"] + n.get("header_pages", [])}) or target["pages"]
        return {"target": node_info(structure, target, document_id, title), "items": items,
                "pagination_complete": end_offset >= total, "boundary_status": boundary,
                "reading_complete": end_offset >= total and boundary == "confirmed",
                "missing_or_uncertain_parts": reasons,
                "verification_pages": [{"page": p, "citation": f"/api/documents/{document_id}#page={p}"}
                                       for p in source_pages],
                "verification_hint": {"target_id": structure["nodes"][0]["id"], "scope": "page",
                                      "pages": sorted({p for origin in source_pages for p in (origin, origin + 1)
                                                       if p <= len(snapshot["parsed"]["pages"])})},
                "next_cursor": encode_cursor(cursor_scope, end_offset) if end_offset < total else None,
                "note": "分页结束仅表示本次范围读完；边界confirmed基于显式结构，不保证OCR/表格语义无误或覆盖远处例外。"}

    async def search(self, query, document_id=None, node_id=None):
        query = query.strip()
        if not query:
            return []
        if len(query) > 300:
            raise ValueError("搜索内容不能超过 300 字，请使用一个具体问题")
        snapshots = await self.snapshots(document_id)
        if node_id:
            key, snapshot, _, _ = self.resolve_node(snapshots, node_id)
            snapshots = {key: snapshots[key]}
        statement = select(Chunk, Document.title).join(Document).where(
            Chunk.document_id.in_(snapshots))
        async with self.db.sessions() as session:
            rows = (await session.execute(statement.order_by(Chunk.document_id, Chunk.ordinal))).all()
        if node_id:
            structure = snapshot["structure"]
            allowed = {n["id"] for n in structure["nodes"] if within(structure, n, node_id)}
            rows = [(c, title) for c, title in rows
                    if snapshot["preview"]["chunks"][c.ordinal]["node_id"] in allowed]
        if not rows:
            return []
        ranks = await asyncio.to_thread(self.rank, query, rows, snapshots)
        grouped = {}
        for index in ranks:
            chunk, title = rows[index]
            snapshot, _ = snapshots[chunk.document_id]
            preview = snapshot["preview"]
            part = preview["chunks"][chunk.ordinal]
            key = part["node_id"]
            if key in grouped:
                grouped[key]["matched_chunk_ids"].append(chunk.id)
                continue
            if len(grouped) == MAX_RESULTS:
                continue
            structure = snapshot["structure"]
            node = get_node(structure, key)
            article = next((n for n in ancestors(structure, node)
                            if n["kind"] == "article" and n["label"].startswith("第")), None)
            article = article or next((n for n in ancestors(structure, node)
                                       if n["kind"] == "article"), None)
            grouped[key] = {"id": chunk.document_id, "title": title, "chunk_id": chunk.id,
                            "node_id": key, "article_id": article["id"] if article else None,
                            "matched_chunk_ids": [chunk.id], "page": part["pages"][0],
                            "pages": part["pages"], "content": chunk.content,
                            "source": part["source"], "kind": node["kind"],
                            "unit_id": node["unit_id"],
                            "excerpt_complete": part["start"] == 0 and part["end"] == len(reading_content(structure, node)["content"]),
                            "boundary_status": node["boundary_status"],
                            "boundary_reasons": node["boundary_reasons"],
                            "citation": f"/api/documents/{chunk.document_id}#page={part['pages'][0]}",
                            "warnings": preview["warnings"] + part["warnings"]}
        logger.info("正文检索 candidates=%d groups=%d", len(rows), len(grouped))
        return list(grouped.values())

    def rank(self, query, rows, snapshots):
        corpus = [terms(chunk.content) for chunk, _ in rows]
        query_terms = terms(query)
        lexical = BM25Okapi(corpus).get_scores(query_terms) if any(corpus) else np.zeros(len(rows))
        query_vector = self.embeddings.query(query)
        matrix = np.stack([np.frombuffer(chunk.vector, dtype=np.float32) for chunk, _ in rows])
        similarities = matrix @ query_vector
        lexical_order = sorted(
            (i for i, words in enumerate(corpus) if set(words).intersection(query_terms)),
            key=lambda i: -lexical[i],
        )
        semantic_order = [int(i) for i in np.argsort(-similarities)
                          if similarities[i] >= MIN_SIMILARITY]
        scores, candidates = {}, set()
        group_ids = [snapshots[c.document_id][0]["preview"]["chunks"][c.ordinal]["node_id"]
                     for c, _ in rows]
        # 每路先限制二十个不同阅读单元；同段子片段只贡献一次排名。
        for ranking in (lexical_order, semantic_order):
            groups = {}
            for index in ranking:
                key = group_ids[index]
                if key not in groups:
                    if len(groups) == 20:
                        continue
                    groups[key] = len(groups) + 1
                    scores[key] = scores.get(key, 0) + 1 / (60 + groups[key])
                candidates.add(index)
        return sorted(candidates, key=lambda i: (-scores[group_ids[i]], -float(similarities[i]), i))
