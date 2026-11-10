"""显式离线重建：先准备可审阅产物，再以单个事务替换派生索引。"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

import numpy as np
from sqlalchemy import delete, select, text

from .config import Settings
from .db import Chunk, Conversation, Database, Document, DocumentParse
from .indexing import INDEX_VERSION
from .knowledge import KnowledgeBase


async def prepare(db, settings, output):
    kb = await KnowledgeBase.create(db, settings)
    output.mkdir(parents=True, exist_ok=True)
    async with db.sessions() as session:
        originals = (await session.execute(select(
            Document.id, Document.filename, Document.sha256, Document.original,
        ).order_by(Document.id))).all()
    manifest = []
    for document_id, filename, digest, raw in originals:
        print(f"解析及建立向量：{filename}", flush=True)
        title, snapshot, vectors = await kb.prepare(filename, raw)
        payload = output / f"{document_id}.json"
        vector_path = output / f"{document_id}.npy"
        payload.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        np.save(vector_path, vectors, allow_pickle=False)
        manifest.append({"id": document_id, "filename": filename, "sha256": digest, "title": title,
                         "snapshot_hash": hashlib.sha256(payload.read_bytes()).hexdigest(),
                         "vectors_hash": hashlib.sha256(vector_path.read_bytes()).hexdigest(),
                         "chunks": len(vectors), "version": INDEX_VERSION})
        print(f"完成：{filename} / {len(vectors)} 片段", flush=True)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


async def apply(db, output):
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    prepared = []
    for entry in manifest:
        payload = output / f"{entry['id']}.json"
        vector_path = output / f"{entry['id']}.npy"
        if (entry["version"] != INDEX_VERSION
                or hashlib.sha256(payload.read_bytes()).hexdigest() != entry["snapshot_hash"]
                or hashlib.sha256(vector_path.read_bytes()).hexdigest() != entry["vectors_hash"]):
            raise ValueError("准备产物版本或哈希不符，请重新准备")
        snapshot = json.loads(payload.read_text(encoding="utf-8"))
        vectors = np.load(vector_path, allow_pickle=False)
        if snapshot["version"] != INDEX_VERSION or len(vectors) != len(snapshot["preview"]["chunks"]):
            raise ValueError("结构片段与向量不一致")
        prepared.append((entry, snapshot, vectors))
    async with db.sessions() as session:
        # SQLite显式写事务，避免检查与替换之间有上传/删除穿插；异常包括删除后的异常均回滚。
        await session.execute(text("BEGIN IMMEDIATE"))
        try:
            documents = {d.id: d for d in await session.scalars(select(Document))}
            if {key: d.sha256 for key, d in documents.items()} != {e["id"]: e["sha256"] for e in manifest}:
                raise ValueError("原资料集合已变化，取消替换；请重新准备")
            if await session.scalar(select(Conversation.id).where(Conversation.status == "running").limit(1)):
                raise ValueError("存在运行中的会话，请先停止后端")
            for entry, _, _ in prepared:
                raw = await session.scalar(select(Document.original).where(Document.id == entry["id"]))
                if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                    raise ValueError("原PDF哈希不符，取消替换")
            await session.execute(delete(Chunk))
            await session.execute(delete(DocumentParse))
            for entry, snapshot, vectors in prepared:
                document = documents[entry["id"]]
                document.title = entry["title"]
                document.preview = snapshot["preview"]["chunks"][0]["content"][:160]
                KnowledgeBase.store_index(session, document, snapshot, vectors)
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
    print(f"已替换 {len(prepared)} 份索引，原PDF、文档ID、账号和会话保留。", flush=True)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", action="store_true")
    action.add_argument("--apply", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=Settings().data_dir)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(data_dir=args.data_dir)  # 不加载.env或外部聊天密钥
    db = Database(settings)
    try:
        if args.prepare:
            await prepare(db, settings, args.output)
        else:
            await apply(db, args.output)
    finally:
        await db.engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
