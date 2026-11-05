import hashlib
import time

from fastapi import HTTPException, Request
from sqlalchemy import select

from .db import Login, User

COOKIE = "banshi_session"


def digest(token: str):
    return hashlib.sha256(token.encode()).hexdigest()


async def current_user(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "请先登录")
    async with request.app.state.db.sessions() as session:
        user = await session.scalar(select(User).join(Login).where(
            Login.token_hash == digest(token), Login.expires > time.time(),
        ))
    if user is None:
        raise HTTPException(401, "登录已过期，请重新登录")
    return user


def public_user(user: User):
    return {"id": user.id, "username": user.username, "name": user.name, "role": user.role}
