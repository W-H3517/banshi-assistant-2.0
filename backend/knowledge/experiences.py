"""经验派生索引与检索；不复用制度排名的低语义门槛，避免小库填满无关结果。"""

import asyncio

import numpy as np
from rank_bm25 import BM25Okapi
from sqlalchemy import delete, select

from ..db import Experience, ExperienceChunk
from .search import fingerprint, terms


def excerpt(item, content):
    result = {"id": item.id, "tags": item.tags,
              "citation": f"/api/experiences/{item.id}/published", "content": content}
    if content != item.content.strip():
        result["partial"] = True
    if item.conflict:
        result.update(conflict=True, conflict_note=item.conflict_note)
    return result


class Experiences:
    async def index_experience(self, session, item):
        await session.execute(delete(ExperienceChunk).where(
            ExperienceChunk.experience_id == item.id))
        if item.status != "published":
            return
        chunks = await asyncio.to_thread(self.embeddings.split, item.content)
        vectors = await asyncio.to_thread(self.embeddings.passages, chunks)
        session.add_all([ExperienceChunk(experience_id=item.id, ordinal=i,
                                        content=chunk["content"], vector=vector.tobytes())
                         for i, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))])

    async def backfill_experiences(self):
        async with self.db.sessions.begin() as session:
            items = await session.scalars(select(Experience).where(
                Experience.status == "published",
                ~select(ExperienceChunk.id).where(
                    ExperienceChunk.experience_id == Experience.id).exists()))
            for item in items:
                await self.index_experience(session, item)

    async def read_experience(self, experience_id):
        async with self.db.sessions() as session:
            item = await session.get(Experience, experience_id)
            if item is None:
                return None
            if item.status != "published":
                raise ValueError("经验未发布或已下架，不可读取")
            return {"experiences": [excerpt(item, item.content.strip())]}

    async def search_experiences(self, queries):
        async with self.db.sessions() as session:
            rows = (await session.execute(select(ExperienceChunk, Experience).join(
                Experience, Experience.id == ExperienceChunk.experience_id).where(
                    Experience.status == "published").order_by(
                        Experience.id, ExperienceChunk.ordinal))).all()
        if not rows:
            return []
        groups = await asyncio.to_thread(self.rank_experiences, queries, rows)
        selected, seen = [], set()
        for rank in range(3):
            for group in groups:
                if rank >= len(group):
                    continue
                chunk, item = rows[group[rank]]
                mark = (item.id, fingerprint(chunk.content))
                if mark not in seen:
                    seen.add(mark)
                    selected.append(excerpt(item, chunk.content))
        return selected

    def rank_experiences(self, queries, rows):
        # 标签参加词法召回；正文向量独立计算，避免标签挤占 512 token 正文预算。
        corpus = [terms(" ".join(item.tags) + " " + chunk.content) for chunk, item in rows]
        matrix = np.stack([np.frombuffer(chunk.vector, dtype=np.float32) for chunk, _ in rows])
        bm25 = BM25Okapi(corpus) if any(corpus) else None
        groups = []
        for query in queries:
            words = set(terms(query))
            similarity = matrix @ self.embeddings.query(query)
            lexical = bm25.get_scores(list(words)) if bm25 else np.zeros(len(rows))
            # 必须有词法交集；向量仅参与候选排序，不用小库的相对第一名证明相关。
            candidates = [i for i, tokens in enumerate(corpus) if words.intersection(tokens)]
            scores = dict.fromkeys(candidates, 0.0)
            for order in (sorted(candidates, key=lambda i: -lexical[i]),
                          sorted(candidates, key=lambda i: -similarity[i])):
                for rank, i in enumerate(order):
                    scores[i] += 1 / (60 + rank + 1)
            chosen, owners = [], set()
            for i in sorted(candidates, key=lambda i: (-scores[i], -similarity[i], i)):
                if rows[i][1].id not in owners:
                    owners.add(rows[i][1].id)
                    chosen.append(i)
                if len(chosen) == 3:
                    break
            groups.append(chosen)
        return groups
