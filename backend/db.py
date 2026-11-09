import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, ForeignKey, LargeBinary, String, Text, event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .config import Settings


def uid():
    return str(uuid4())


def now():
    return datetime.now(UTC).isoformat()


def password_hash(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"{salt}:{digest}"


def check_password(password: str, encoded: str):
    return hmac.compare_digest(password_hash(password, encoded.split(":")[0]), encoded)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str]
    role: Mapped[str]
    password: Mapped[str]


class Login(Base):
    __tablename__ = "logins"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires: Mapped[float]


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    title: Mapped[str]
    filename: Mapped[str]
    sha256: Mapped[str] = mapped_column(String, unique=True)
    page_count: Mapped[int]
    chunk_count: Mapped[int]
    preview: Mapped[str]
    original: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    created_at: Mapped[str] = mapped_column(default=now)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class SeedImport(Base):
    __tablename__ = "seed_imports"
    sha256: Mapped[str] = mapped_column(String, primary_key=True)


class DocumentParse(Base):
    """扫描/混合 PDF 的解析快照，与原文及检索片段同事务保存。"""

    __tablename__ = "document_parses"
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)


class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    page: Mapped[int]
    ordinal: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    vector: Mapped[bytes] = mapped_column(LargeBinary)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(default="新对话")
    model: Mapped[str]
    updated_at: Mapped[str] = mapped_column(default=now)
    status: Mapped[str] = mapped_column(default="idle")
    history: Mapped[list] = mapped_column(JSON, default=list)
    transcript: Mapped[list] = mapped_column(JSON, default=list)
    pending: Mapped[list] = mapped_column(JSON, default=list)
    requests: Mapped[int] = mapped_column(default=0)
    error: Mapped[str] = mapped_column(default="")


class Database:
    def __init__(self, settings: Settings):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{(settings.data_dir / 'banshi.sqlite3').as_posix()}",
            connect_args={"timeout": 15},
        )

        @event.listens_for(self.engine.sync_engine, "connect")
        def pragmas(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def initialize(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions() as session:
            if not await session.scalar(select(User.id).limit(1)):
                users = [User(username=username, name=name, role=role,
                              password=password_hash("demo12345"))
                         for username, name, role in [
                             ("admin", "制度管理员", "admin"),
                             ("alice", "员工小林", "employee"),
                             ("bob", "员工小陈", "employee"),
                         ]]
                session.add_all(users)
            await session.execute(update(Conversation).where(
                Conversation.status == "running"
            ).values(status="interrupted", error="服务重启中断了生成，请重新发送或继续回答。"))
            await session.commit()
