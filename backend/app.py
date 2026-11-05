import asyncio
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .agent import create_agent
from .auth import COOKIE, current_user, digest, public_user
from .chat import ChatService, detail, owned
from .config import ROOT, Settings
from .db import Conversation, Database, Document, Login, User, check_password
from .knowledge import DuplicateDocument, KnowledgeBase, public_document
from .logging_setup import LoggingRuntime
from .pdf import MAX_FILE_BYTES, InvalidPDF

UserDep = Annotated[User, Depends(current_user)]


class Credentials(BaseModel):
    username: str = Field(max_length=80)
    password: str = Field(max_length=200)


class Turn(BaseModel):
    prompt: str = Field(default="", max_length=12000)
    answers: dict[str, str] = Field(default_factory=dict)


def create_app(settings=None, *, agent_factory=create_agent):
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app):
        logs = LoggingRuntime(settings)
        db = Database(settings)
        try:
            await db.initialize()
            app.state.db = db
            db.knowledge = await KnowledgeBase.create(db, settings)
            await db.knowledge.seed()
            app.state.chat = ChatService(db, settings, agent_factory, logs.instrumentation)
            try:
                yield
            finally:
                await app.state.chat.close()
        finally:
            await db.engine.dispose()
            logs.close()

    app = FastAPI(title="办事助手", lifespan=lifespan)

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if (request.method not in {"GET", "HEAD", "OPTIONS"} and origin
                and urlsplit(origin).netloc != request.headers.get("host")):
            return JSONResponse({"detail": "不允许跨站请求"}, status_code=403)
        response = await call_next(request)
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/health")
    async def health():
        return {"ok": True, "model_configured": bool(settings.api_key)}

    @app.post("/api/login")
    async def login(body: Credentials, response: Response):
        async with app.state.db.sessions() as session:
            user = await session.scalar(select(User).where(User.username == body.username))
            if user is None or not await asyncio.to_thread(
                check_password, body.password, user.password,
            ):
                raise HTTPException(401, "账号或密码错误")
            token = secrets.token_urlsafe(32)
            session.add(Login(token_hash=digest(token), user_id=user.id,
                              expires=time.time() + 7 * 86400))
            await session.commit()
        response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=7 * 86400)
        return public_user(user)

    @app.post("/api/logout")
    async def logout(request: Request, response: Response):
        async with app.state.db.sessions() as session:
            await session.execute(delete(Login).where(
                Login.token_hash == digest(request.cookies.get(COOKIE, "")),
            ))
            await session.commit()
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    async def me(user: UserDep):
        return public_user(user)

    @app.get("/api/documents")
    async def documents(user: UserDep, q: str = ""):
        try:
            return await app.state.db.knowledge.documents(q)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/api/documents", status_code=201)
    async def upload(file: UploadFile, user: UserDep):
        if user.role != "admin":
            raise HTTPException(403, "仅管理员可以上传制度")
        filename = Path((file.filename or "").replace("\\", "/")).name
        if not filename.lower().endswith(".pdf"):
            raise HTTPException(422, "请上传文字版 .pdf 制度文件")
        raw = await file.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(413, "单份 PDF 不能超过 20 MB")
        try:
            return await app.state.db.knowledge.upload(filename, raw, user.id)
        except DuplicateDocument as exc:
            raise HTTPException(409, str(exc)) from None
        except InvalidPDF as exc:
            raise HTTPException(422, str(exc)) from None
        except Exception:
            logging.getLogger(__name__).exception("PDF 索引失败，上传未提交")
            raise HTTPException(503, "PDF 解析或索引失败，文件未入库，请稍后重试") from None

    @app.get("/api/documents/{document_id}")
    async def document(document_id: str, user: UserDep):
        async with app.state.db.sessions() as session:
            doc = await session.get(Document, document_id)
        if doc is None:
            raise HTTPException(404, "制度不存在")
        return public_document(doc)

    @app.get("/api/documents/{document_id}/file")
    async def original_pdf(document_id: str, user: UserDep):
        async with app.state.db.sessions() as session:
            raw = await session.scalar(select(Document.original).where(Document.id == document_id))
        if raw is None:
            raise HTTPException(404, "制度不存在")
        return Response(raw, media_type="application/pdf", headers={
            "Content-Disposition": 'inline; filename="document.pdf"',
            "X-Content-Type-Options": "nosniff",
        })

    @app.get("/api/conversations")
    async def conversations(user: UserDep):
        async with app.state.db.sessions() as session:
            rows = await session.scalars(select(Conversation).where(
                Conversation.user_id == user.id,
            ).order_by(Conversation.updated_at.desc()))
            return [{key: getattr(row, key) for key in ("id", "title", "status", "updated_at")}
                    for row in rows]

    @app.post("/api/conversations", status_code=201)
    async def new_conversation(user: UserDep):
        row = Conversation(user_id=user.id, model=settings.model)
        async with app.state.db.sessions() as session:
            session.add(row)
            await session.commit()
        return detail(row)

    @app.get("/api/conversations/{conversation_id}")
    async def conversation(conversation_id: str, user: UserDep):
        return detail(await owned(app.state.db, conversation_id, user.id))

    @app.post("/api/conversations/{conversation_id}/turn")
    async def turn(conversation_id: str, body: Turn, user: UserDep):
        if any(len(value) > 12000 for value in body.answers.values()):
            raise HTTPException(422, "回答过长")
        stream = await app.state.chat.start(user, conversation_id, body.prompt, body.answers)
        return StreamingResponse(stream, media_type="text/event-stream", headers={
            "X-Accel-Buffering": "no", "Cache-Control": "no-cache",
        })

    @app.post("/api/conversations/{conversation_id}/stop")
    async def stop(conversation_id: str, user: UserDep):
        await owned(app.state.db, conversation_id, user.id)
        await app.state.chat.stop(conversation_id)
        return {"ok": True}

    frontend = ROOT / "frontend/dist"
    if frontend.exists():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app
