"""资料库维护：上传、删除、初始资料与重置，以及派生索引的保存和版本检查。"""

import asyncio
import hashlib
import logging

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from ..config import ROOT
from ..db import Chunk, Conversation, Document, DocumentIndex, SeedImport, User
from ..indexing import INDEX_VERSION, prepare_document
from .outline import normalize_label

logger = logging.getLogger(__name__)
FIXTURES = ROOT / "data/fixtures"
INDEX_COLUMNS = ("version", "generation", "meta", "nodes", "units", "tables", "blocks", "rules")
CHUNK_COLUMNS = ("ordinal", "node_id", "content")
DUPLICATE = "该 PDF 已在知识库中，无需重复上传"


# 普通员工在切分预览里能看到的片段字段；其余为管理员诊断信息。
READER_FIELDS = ("ordinal", "node_id", "parent_id", "title", "chapter", "source", "pages",
                 "content", "regions", "row", "header_pages")


class DuplicateDocument(ValueError):
    pass


def public_document(doc):
    return {key: getattr(doc, key) for key in (
        "id", "title", "filename", "created_at", "preview", "page_count", "chunk_count",
    )}


def new_document(title, filename, raw, snapshot, user_id):
    return Document(
        title=title, filename=filename, sha256=hashlib.sha256(raw).hexdigest(),
        page_count=len(snapshot["meta"]["pages"]), chunk_count=len(snapshot["chunks"]),
        preview=snapshot["chunks"][0]["content"][:160], original=raw, uploaded_by=user_id,
    )


class Library:
    async def prepare(self, filename, raw):
        """解析并计算向量，不写库；失败时不留下任何半成品。"""
        title, snapshot = await asyncio.to_thread(
            prepare_document, raw, filename, self.embeddings, self.ocr,
        )
        vectors = await asyncio.to_thread(self.embeddings.passages, snapshot["chunks"])
        return title, snapshot, vectors

    @staticmethod
    def store_index(session, document, snapshot, vectors):
        document.chunk_count = len(snapshot["chunks"])
        session.add(DocumentIndex(document_id=document.id,
                                  **{key: snapshot[key] for key in INDEX_COLUMNS}))
        session.add_all([Chunk(
            document_id=document.id, page=chunk["pages"][0], vector=vector.tobytes(),
            **{key: chunk[key] for key in CHUNK_COLUMNS},
            detail={key: value for key, value in chunk.items() if key not in CHUNK_COLUMNS},
        ) for chunk, vector in zip(snapshot["chunks"], vectors, strict=True)])

    async def check_index(self):
        async with self.db.sessions() as session:
            versions = (await session.execute(
                select(Document.id, DocumentIndex.version).outerjoin(DocumentIndex))).all()
        if any(version != INDEX_VERSION for _, version in versions):
            raise ValueError("发现旧索引；本版本不兼容或自动迁移旧数据，请按开发约定处理旧运行库")

    async def seed(self):
        async with self.db.sessions() as session:
            admin = await session.scalar(select(User).where(User.username == "admin"))
            existing = set(await session.scalars(select(Document.sha256)))
            imported = set(await session.scalars(select(SeedImport.sha256)))
        for path in sorted(FIXTURES.glob("*.pdf")):
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest in imported:
                continue
            if digest not in existing:
                await self.upload(path.name, raw, admin.id)
            async with self.db.sessions() as session:
                session.add(SeedImport(sha256=digest))
                await session.commit()

    async def upload(self, filename, raw, user_id):
        digest = hashlib.sha256(raw).hexdigest()
        async with self.upload_lock:
            async with self.db.sessions() as session:
                if await session.scalar(select(Document.id).where(Document.sha256 == digest)):
                    raise DuplicateDocument(DUPLICATE)
            title, snapshot, vectors = await self.prepare(filename, raw)
            document = new_document(title, filename, raw, snapshot, user_id)
            async with self.db.sessions() as session:
                session.add(document)
                try:
                    await session.flush()
                    self.store_index(session, document, snapshot, vectors)
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    raise DuplicateDocument(DUPLICATE) from None
            logger.info("PDF 索引完成 document_id=%s pages=%d chunks=%d",
                        document.id, document.page_count, document.chunk_count)
            return public_document(document)

    async def delete_document(self, document_id):
        async with self.upload_lock, self.db.sessions() as session:
            if await session.get(Document, document_id) is None:
                return False
            await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
            await session.execute(delete(DocumentIndex).where(
                DocumentIndex.document_id == document_id))
            await session.execute(delete(Document).where(Document.id == document_id))
            await session.commit()
        self.term_cache.clear()
        return True

    async def reset_demo(self, user_id):
        async with self.upload_lock:
            prepared = []
            for path in sorted(FIXTURES.glob("*.pdf")):
                raw = path.read_bytes()
                title, snapshot, vectors = await self.prepare(path.name, raw)
                prepared.append((new_document(title, path.name, raw, snapshot, user_id),
                                 snapshot, vectors))
            if len(prepared) != 3:
                raise ValueError("初始模拟制度应为三份，重置已取消")
            async with self.db.sessions.begin() as session:
                for model in (Conversation, Chunk, DocumentIndex, Document, SeedImport):
                    await session.execute(delete(model))
                for document, snapshot, vectors in prepared:
                    session.add(document)
                    await session.flush()
                    self.store_index(session, document, snapshot, vectors)
                    session.add(SeedImport(sha256=document.sha256))
            self.term_cache.clear()
        return {"ok": True, "document_count": len(prepared)}

    async def documents(self, query=""):
        """资料列表；带关键词时按名称匹配优先，其后是正文命中的文件。"""
        hits = await self.search(query) if query.strip() else []
        async with self.db.sessions() as session:
            docs = list(await session.scalars(select(Document).order_by(Document.created_at.desc())))
        if not query.strip():
            return [public_document(doc) for doc in docs]
        label = normalize_label(query)
        named = [doc.id for doc in docs
                 if label in normalize_label(doc.title) or label in normalize_label(doc.filename)]
        by_id = {doc.id: doc for doc in docs}
        first_hit = {}
        for hit in hits:
            first_hit.setdefault(hit["id"], hit)
        result = []
        for key in dict.fromkeys(named + list(first_hit)):
            hit = first_hit.get(key)
            result.append({**public_document(by_id[key]),
                           "preview": hit["content"][:160] if hit else by_id[key].preview,
                           "match_page": hit["page"] if hit else 1})
        return result

    async def preview(self, document_id, diagnostics):
        """切分预览：实际入库的检索片段及其高亮区域；文档不存在时返回 None。

        所有登录用户都能看片段的正文、所属和位置；diagnostics 为真（管理员）时另带
        token 数、阅读边界、定位状态等诊断信息。parent_id 用于把整表的引用对应到各行片段。
        """
        async with self.db.sessions() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return None
            version, meta, nodes = (await session.execute(select(
                DocumentIndex.version, DocumentIndex.meta, DocumentIndex.nodes,
            ).where(DocumentIndex.document_id == document_id))).one()
            chunks = (await session.execute(select(
                Chunk.ordinal, Chunk.node_id, Chunk.content, Chunk.detail,
            ).where(Chunk.document_id == document_id).order_by(Chunk.ordinal))).all()
        parents = {node["id"]: node["parent_id"] for node in nodes}
        items = [{**chunk.detail, "ordinal": chunk.ordinal, "node_id": chunk.node_id,
                  "parent_id": parents[chunk.node_id], "content": chunk.content} for chunk in chunks]
        result = {"pages": meta["pages"], "ocr_pages": meta["ocr_pages"], "warnings": meta["warnings"],
                  "document": public_document(document)}
        if diagnostics:
            return {**result, "version": version, "removed_line_count": meta["removed_line_count"],
                    "chunks": items}
        return {**result, "chunks": [{key: item[key] for key in READER_FIELDS if key in item}
                                     for item in items]}
