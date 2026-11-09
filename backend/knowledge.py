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

    async def rebuild(self):
        """全部准备成功后，以一个事务替换旧切分/索引；不保留旧版读取分支。"""
        async with self.upload_lock:
            async with self.db.sessions() as session:
                documents = list(await session.scalars(select(Document).order_by(Document.id)))
                snapshots = {p.document_id: p.payload for p in await session.scalars(select(DocumentParse))}
                if all(snapshots.get(d.id, {}).get("version") == INDEX_VERSION for d in documents):
                    return 0
                originals = {d.id: await session.scalar(select(Document.original).where(Document.id == d.id))
                             for d in documents}
            prepared = []
            for document in documents:
                title, snapshot, vectors = await self.prepare(document.filename, originals[document.id])
                prepared.append((document.id, title, snapshot, vectors))
            async with self.db.sessions.begin() as session:
                await session.execute(delete(Chunk))
                await session.execute(delete(DocumentParse))
                for document_id, title, snapshot, vectors in prepared:
                    document = await session.get(Document, document_id)
                    document.title = title
                    document.preview = snapshot["preview"]["chunks"][0]["content"][:160]
                    self.store_index(session, document, snapshot, vectors)
            logger.info("结构上下文索引重建完成 documents=%d version=%s", len(prepared), INDEX_VERSION)
            return len(prepared)

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
            ids = list(dict.fromkeys(hit["id"] for hit in hits))
            async with self.db.sessions() as session:
                docs = {doc.id: doc for doc in await session.scalars(
                    select(Document).where(Document.id.in_(ids)),
                )}
            return [{**public_document(docs[key]),
                     "preview": next(hit["content"][:160] for hit in hits if hit["id"] == key),
                     "match_page": next(hit["page"] for hit in hits if hit["id"] == key)}
                    for key in ids]
        async with self.db.sessions() as session:
            return [public_document(doc) for doc in await session.scalars(
                select(Document).order_by(Document.created_at.desc()),
            )]

    async def search(self, query, document_id=None, page=None):
        query = query.strip()
        if not query:
            return []
        if len(query) > 300:
            raise ValueError("搜索内容不能超过 300 字，请使用一个具体问题")
        statement = select(Chunk, Document.title).join(Document)
        if document_id:
            statement = statement.where(Chunk.document_id == document_id)
        if page is not None:
            if not document_id or page < 1:
                raise ValueError("指定页码时须同时指定有效文档 ID，页码从 1 开始")
            async with self.db.sessions() as session:
                parsed = await session.get(DocumentParse, document_id)
            ordinals = ([c["ordinal"] for c in parsed.payload["preview"]["chunks"]
                         if page in c["pages"]] if parsed else [])
            statement = statement.where(Chunk.ordinal.in_(ordinals))
        async with self.db.sessions() as session:
            rows = (await session.execute(statement.order_by(
                Chunk.document_id, Chunk.ordinal,
            ))).all()
        if not rows:
            return []
        result = await asyncio.to_thread(self.rank, query, rows, page is not None)
        async with self.db.sessions() as session:
            snapshots = {p.document_id: p.payload["preview"] for p in await session.scalars(
                select(DocumentParse).where(DocumentParse.document_id.in_({hit["id"] for hit in result})),
            )}
        ordinals = {chunk.id: chunk.ordinal for chunk, _ in rows}
        for hit in result:
            snapshot = snapshots.get(hit["id"])
            chunk = snapshot["chunks"][ordinals[hit["chunk_id"]]]
            hit.update(pages=chunk["pages"], source=chunk["source"], body=chunk["content"],
                       warnings=snapshot["warnings"] + chunk["warnings"])
            if page is not None:
                hit["page"] = page
                hit["citation"] = f"/api/documents/{hit['id']}#page={page}"
        logger.info("知识检索 candidates=%d hits=%d", len(rows), len(result))
        return result

    def rank(self, query, rows, exact_page=False):
        corpus = [terms(chunk.content) for chunk, _ in rows]
        query_terms = terms(query)
        lexical = BM25Okapi(corpus).get_scores(query_terms) if any(corpus) else np.zeros(len(rows))
        query_vector = self.embeddings.query(query)
        matrix = np.stack([np.frombuffer(chunk.vector, dtype=np.float32) for chunk, _ in rows])
        similarities = matrix @ query_vector
        lexical_order = sorted(
            (i for i, words in enumerate(corpus) if set(words).intersection(query_terms)),
            key=lambda i: -lexical[i],
        )[:20]
        semantic_order = [int(i) for i in np.argsort(-similarities)
                          if similarities[i] >= MIN_SIMILARITY][:20]
        scores = {}
        for ranking in (lexical_order, semantic_order):
            for rank, index in enumerate(ranking, 1):
                scores[index] = scores.get(index, 0) + 1 / (60 + rank)
        # 指定页码时查阅该页，避免相似度过滤掉同页的限制条件。
        indices = sorted(
            scores, key=lambda i: (-scores[i], -float(similarities[i])),
        )
        if exact_page:
            indices.extend(i for i in range(len(rows)) if i not in scores)
        result = []
        for index in indices[:MAX_RESULTS]:
            chunk, title = rows[index]
            result.append({"id": chunk.document_id, "title": title,
                           "chunk_id": chunk.id, "page": chunk.page,
                           "content": chunk.content,
                           "citation": f"/api/documents/{chunk.document_id}#page={chunk.page}"})
        return result
