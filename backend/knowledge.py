import asyncio
import hashlib
import logging
import re

import jieba
import numpy as np
from rank_bm25 import BM25Okapi
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from .config import ROOT
from .db import Chunk, Conversation, Document, DocumentParse, SeedImport, User
from .embeddings import LocalEmbeddings
from .ocr import LocalOCR, indexed_preview
from .pdf import extract_pages

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

    async def reset_demo(self, user_id):
        async with self.upload_lock:
            # 先完成解析和向量生成，全部成功后才在同一事务中替换现有资料。
            prepared = []
            for path in sorted((ROOT / "data/fixtures").glob("*.pdf")):
                raw = path.read_bytes()
                pages = await asyncio.to_thread(extract_pages, raw)
                title = next(text for text in pages if text).splitlines()[0][:160]
                chunks = await asyncio.to_thread(self.embeddings.split, title, pages)
                vectors = await asyncio.to_thread(self.embeddings.passages, title, chunks)
                prepared.append((Document(
                    title=title, filename=path.name, sha256=hashlib.sha256(raw).hexdigest(),
                    page_count=len(pages), chunk_count=len(chunks),
                    preview=next(text for text in pages if text)[:160],
                    original=raw, uploaded_by=user_id,
                ), chunks, vectors))
            if len(prepared) != 3:
                raise ValueError("初始模拟制度应为三份，重置已取消")
            async with self.db.sessions.begin() as session:
                for model in (Conversation, Chunk, DocumentParse, Document, SeedImport):
                    await session.execute(delete(model))
                for document, chunks, vectors in prepared:
                    session.add(document)
                    await session.flush()
                    session.add_all([
                        Chunk(document_id=document.id, **chunk, vector=vector.tobytes())
                        for chunk, vector in zip(chunks, vectors, strict=True)
                    ])
                    session.add(SeedImport(sha256=document.sha256))
        return {"ok": True, "document_count": len(prepared)}

    async def upload(self, filename, raw, user_id):
        digest = hashlib.sha256(raw).hexdigest()
        async with self.upload_lock:
            async with self.db.sessions() as session:
                if await session.scalar(select(Document.id).where(Document.sha256 == digest)):
                    raise DuplicateDocument("该 PDF 已在知识库中，无需重复上传")
            scan_pages = []
            pages = await asyncio.to_thread(extract_pages, raw, scan_pages=scan_pages)
            snapshot = None
            if scan_pages:
                parsed = await asyncio.to_thread(self.ocr.parse, raw, scan_pages)
                title = parsed["sections"][0]["content"][:160]
                preview = await asyncio.to_thread(indexed_preview, parsed, title, self.embeddings)
                snapshot = {"parsed": parsed, "preview": preview}
                chunks = [{"page": c["pages"][0], "ordinal": c["ordinal"], "content": c["content"]}
                          for c in preview["chunks"]]
                pages = ["\n".join(line["text"] for line in p["lines"]) for p in parsed["pages"]]
            else:
                title = next(text for text in pages if text).splitlines()[0][:160]
                chunks = await asyncio.to_thread(self.embeddings.split, title, pages)
            vectors = await asyncio.to_thread(self.embeddings.passages, title, chunks)
            document = Document(
                title=title, filename=filename, sha256=digest, page_count=len(pages),
                chunk_count=len(chunks), preview=next(text for text in pages if text)[:160],
                original=raw, uploaded_by=user_id,
            )
            async with self.db.sessions() as session:
                session.add(document)
                try:
                    await session.flush()
                    if snapshot is not None:
                        session.add(DocumentParse(document_id=document.id, payload=snapshot))
                    session.add_all([
                        Chunk(document_id=document.id, **chunk, vector=vector.tobytes())
                        for chunk, vector in zip(chunks, vectors, strict=True)
                    ])
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    raise DuplicateDocument("该 PDF 已在知识库中，无需重复上传") from None
            logger.info("PDF 索引完成 document_id=%s pages=%d chunks=%d",
                        document.id, len(pages), len(chunks))
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
            statement = statement.where(or_(Chunk.page == page, Chunk.ordinal.in_(ordinals)))
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
            if snapshot:
                chunk = snapshot["chunks"][ordinals[hit["chunk_id"]]]
                hit["pages"] = chunk["pages"]
                hit["warnings"] = snapshot["warnings"] + chunk["warnings"]
                if page is not None and page in chunk["pages"]:
                    hit["page"] = page
                    hit["citation"] = f"/api/documents/{hit['id']}#page={page}"
        logger.info("知识检索 candidates=%d hits=%d", len(rows), len(result))
        return result

    def rank(self, query, rows, exact_page=False):
        corpus = [terms(title + " " + chunk.content) for chunk, title in rows]
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
