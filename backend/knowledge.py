import re

from sqlalchemy import select

from .db import Database, Document


async def search_documents(db: Database, query: str, *, full: bool = False):
    terms = [term.casefold() for term in re.split(r"[\s，,。；;、]+", query.strip()) if term]
    async with db.sessions() as session:
        documents = list(await session.scalars(select(Document).order_by(Document.created_at.desc())))
    scored = [(sum(doc.content.casefold().count(term) for term in terms), doc)
              for doc in documents]
    ranked = [doc for score, doc in sorted(scored, key=lambda item: -item[0]) if score or not terms]
    if full:
        ranked = ranked[:5]
    return [{"id": doc.id, "title": doc.title, "filename": doc.filename,
             "created_at": doc.created_at,
             **({"content": doc.content} if full else {"preview": doc.content[:160]})}
            for doc in ranked]
