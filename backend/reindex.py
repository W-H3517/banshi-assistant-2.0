"""用库中保存的原 PDF 重建全部检索索引：uv run --locked python -m backend.reindex。

先逐份解析并计算向量，全部成功后在一个事务里替换派生索引；任何一步失败都不改动运行库。
原 PDF、文档 ID、账号、会话和经验保留。须在后端停止时运行（端口被占用则拒绝）。
"""

import argparse
import asyncio
import hashlib
from pathlib import Path

from sqlalchemy import delete, select, text

from .config import Settings
from .db import Chunk, Database, Document, DocumentIndex
from .knowledge import KnowledgeBase
from .preflight import PORT, check_port

LEGACY_TABLES = ("chunks", "document_parses")  # 更早的索引表，替换时一并移除


async def rebuild(db, settings):
    kb = await KnowledgeBase.create(db, settings)
    async with db.sessions() as session:
        originals = (await session.execute(select(
            Document.id, Document.filename, Document.sha256, Document.original,
        ).order_by(Document.id))).all()
    prepared = []
    for number, (document_id, filename, digest, raw) in enumerate(originals, 1):
        print(f"[{number}/{len(originals)}] 解析及建立向量：{filename}", flush=True)
        title, snapshot, vectors = await kb.prepare(filename, raw)
        prepared.append((document_id, digest, title, snapshot, vectors))
    async with db.sessions() as session:
        # SQLite 显式写事务，避免检查与替换之间有上传/删除穿插；异常（含删除后的异常）均回滚。
        await session.execute(text("BEGIN IMMEDIATE"))
        try:
            documents = {d.id: d for d in await session.scalars(select(Document))}
            if {key: d.sha256 for key, d in documents.items()} != {p[0]: p[1] for p in prepared}:
                raise ValueError("重建期间资料有变化，已取消替换；请重新运行")
            stored = (await session.execute(select(Document.sha256, Document.original))).all()
            if any(hashlib.sha256(raw).hexdigest() != digest for digest, raw in stored):
                raise ValueError("原 PDF 与记录的哈希不符，已取消替换")
            await session.execute(delete(Chunk))
            await session.execute(delete(DocumentIndex))
            for legacy in LEGACY_TABLES:
                await session.execute(text(f"DROP TABLE IF EXISTS {legacy}"))
            for document_id, _, title, snapshot, vectors in prepared:
                document = documents[document_id]
                document.title = title
                document.preview = snapshot["chunks"][0]["content"][:160]
                KnowledgeBase.store_index(session, document, snapshot, vectors)
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
    print(f"已重建 {len(prepared)} 份索引，原 PDF、文档 ID、账号、会话和经验保留。", flush=True)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Settings().data_dir)
    args = parser.parse_args()
    settings = Settings(data_dir=args.data_dir)  # 不加载 .env 或外部聊天密钥
    db = Database(settings)
    if check_port():
        raise SystemExit(f"后端正在运行（端口 {PORT} 被占用），请先停止后端再重建。")
    try:
        # 与后端启动相同：建表，并把异常退出遗留的“生成中”会话标为中断，避免挡住重建。
        await db.initialize()
        await rebuild(db, settings)
    finally:
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
