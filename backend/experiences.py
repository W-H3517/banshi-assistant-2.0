"""员工经验：提交、我的记录、管理员审核发布，以及已发布经验的浏览。

状态：draft 草稿 → pending 待审核 → verifying 待核实 / published 已发布 / rejected 不发布；
已发布可下架为 withdrawn，下架后可重新发布。草稿仅本人可见，提交后本人和管理员可见，
发布后全员可见且不显示提交人。
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy import select

from .auth import current_user
from .db import Experience, ExperienceEvent, User, now

UserDep = Annotated[User, Depends(current_user)]
ANONYMOUS = "某同事"
# 管理员各操作允许的起始状态及结果状态；save 只调整内容，不改变状态。
TRANSITIONS = {
    "verify": (("pending", "verifying"), "verifying"),
    "publish": (("pending", "verifying", "withdrawn"), "published"),
    "reject": (("pending", "verifying"), "rejected"),
    "withdraw": (("published",), "withdrawn"),
    "save": (("pending", "verifying", "published", "rejected", "withdrawn"), None),
}


def clean_text(value: str):
    value = value.strip()
    if not value:
        raise ValueError("内容不能为空")
    return value


def clean_tags(values: list[str]):
    tags = list(dict.fromkeys(tag.strip() for tag in values if tag.strip()))
    if not tags:
        raise ValueError("请至少填写一个标签")
    if any(len(tag) > 30 for tag in tags):
        raise ValueError("单个标签不超过 30 字")
    return tags


Text = Annotated[str, Field(max_length=5000), AfterValidator(clean_text)]
Tags = Annotated[list[str], Field(max_length=8), AfterValidator(clean_tags)]
Note = Annotated[str, Field(max_length=1000)]


class Draft(BaseModel):
    text: Text
    tags: Tags
    submit: bool = False


class Review(BaseModel):
    action: Literal["verify", "publish", "reject", "withdraw", "save"]
    content: Text | None = None
    tags: Tags | None = None
    conflict: bool | None = None
    conflict_note: Note | None = None
    admin_note: Note | None = None  # 给提交人的留言
    note: Note = ""                  # 本次操作的核实或处理记录，仅管理员可见


def public_view(item):
    """全员可见的发布内容：不含提交人、原文和审核记录。"""
    return {"id": item.id, "tags": item.tags, "content": item.content, "author": ANONYMOUS,
            "published_at": item.published_at, "conflict": item.conflict,
            "conflict_note": item.conflict_note}


def own_view(item):
    """提交人看到的自己的记录：原文、状态和管理员留言；不反馈管理员的改动过程。"""
    return {"id": item.id, "status": item.status, "tags": item.tags, "text": item.original,
            "admin_note": item.admin_note, "created_at": item.created_at,
            "updated_at": item.updated_at, "submitted_at": item.submitted_at,
            "published_at": item.published_at,
            "content": item.content if item.status == "published" else None}


def admin_view(item, author, events=()):
    return {**own_view(item), "content": item.content, "author": author,
            "conflict": item.conflict, "conflict_note": item.conflict_note,
            "events": [{"action": e.action, "note": e.note, "content": e.content,
                        "actor": actor, "created_at": e.created_at} for e, actor in events]}


def submit(session, item, actor):
    item.status, item.submitted_at, item.content = "pending", now(), item.original
    session.add(ExperienceEvent(experience_id=item.id, actor_id=actor.id, action="submit",
                                content=item.original))


def matches(item, query, tag):
    """关键词须全部出现在内容或标签中；标签筛选为完全相同。"""
    haystack = (item.content + " " + " ".join(item.tags)).casefold()
    return (not tag or tag in item.tags) and all(
        word in haystack for word in query.casefold().split())


router = APIRouter(prefix="/api")


def require_admin(user):
    if user.role != "admin":
        raise HTTPException(403, "仅管理员可以审核经验")


async def own_draft(session, experience_id, user):
    item = await session.get(Experience, experience_id)
    if item is None or item.author_id != user.id:
        raise HTTPException(404, "经验不存在")
    if item.status != "draft":
        raise HTTPException(409, "已提交的经验不能再修改；如需补充请另记一条")
    return item


@router.post("/experiences", status_code=201)
async def create(body: Draft, user: UserDep, request: Request):
    item = Experience(author_id=user.id, tags=body.tags, original=body.text)
    async with request.app.state.db.sessions() as session:
        session.add(item)
        await session.flush()
        if body.submit:
            submit(session, item, user)
        await session.commit()
    return own_view(item)


@router.put("/experiences/{experience_id}")
async def update(experience_id: str, body: Draft, user: UserDep, request: Request):
    async with request.app.state.db.sessions() as session:
        item = await own_draft(session, experience_id, user)
        item.original, item.tags, item.updated_at = body.text, body.tags, now()
        if body.submit:
            submit(session, item, user)
        await session.commit()
    return own_view(item)


@router.delete("/experiences/{experience_id}")
async def remove(experience_id: str, user: UserDep, request: Request):
    async with request.app.state.db.sessions() as session:
        await session.delete(await own_draft(session, experience_id, user))
        await session.commit()
    return {"ok": True}


@router.get("/experiences/mine")
async def mine(user: UserDep, request: Request):
    async with request.app.state.db.sessions() as session:
        rows = await session.scalars(select(Experience).where(
            Experience.author_id == user.id).order_by(Experience.updated_at.desc()))
        return [own_view(item) for item in rows]


@router.get("/experiences")
async def published(user: UserDep, request: Request, q: str = "", tag: str = ""):
    async with request.app.state.db.sessions() as session:
        rows = list(await session.scalars(select(Experience).where(
            Experience.status == "published").order_by(Experience.published_at.desc())))
    return {"items": [public_view(item) for item in rows if matches(item, q, tag)],
            "tags": sorted({name for item in rows for name in item.tags})}


@router.get("/experiences/{experience_id}")
async def detail(experience_id: str, user: UserDep, request: Request):
    async with request.app.state.db.sessions() as session:
        item = await session.get(Experience, experience_id)
    if item is not None and item.status == "published":
        return public_view(item)
    if item is not None and item.author_id == user.id:
        return own_view(item)
    raise HTTPException(404, "经验不存在或尚未发布")


@router.get("/admin/experiences")
async def review_list(user: UserDep, request: Request):
    """管理工作台：全部已提交的经验及其操作记录；草稿不在其中。"""
    require_admin(user)
    async with request.app.state.db.sessions() as session:
        rows = (await session.execute(select(Experience, User.name).join(
            User, User.id == Experience.author_id).where(Experience.status != "draft").order_by(
            Experience.submitted_at.desc()))).all()
        events = {}
        for event, actor in (await session.execute(select(ExperienceEvent, User.name).join(
                User, User.id == ExperienceEvent.actor_id).order_by(ExperienceEvent.created_at))).all():
            events.setdefault(event.experience_id, []).append((event, actor))
    return [admin_view(item, author, events.get(item.id, [])) for item, author in rows]


@router.post("/admin/experiences/{experience_id}/review")
async def review(experience_id: str, body: Review, user: UserDep, request: Request):
    require_admin(user)
    allowed, target = TRANSITIONS[body.action]
    async with request.app.state.db.sessions() as session:
        item = await session.get(Experience, experience_id)
        if item is None or item.status == "draft":
            raise HTTPException(404, "经验不存在")
        if item.status not in allowed:
            raise HTTPException(409, "当前状态不能执行此操作，请刷新后重试")
        for field in ("content", "tags", "conflict", "conflict_note", "admin_note"):
            if (value := getattr(body, field)) is not None:
                setattr(item, field, value)
        if not item.conflict:
            item.conflict_note = ""
        if target:
            item.status = target
        if target == "published":
            item.published_at = now()
        item.updated_at = now()
        session.add(ExperienceEvent(experience_id=item.id, actor_id=user.id, action=body.action,
                                    note=body.note, content=item.content))
        author = await session.scalar(select(User.name).where(User.id == item.author_id))
        await session.commit()
    return admin_view(item, author)
