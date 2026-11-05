from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from pydantic_ai.messages import ModelMessage


class Identity(BaseModel):
    user_id: UUID = Field(default_factory=uuid4)


class Session(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    model: str
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    messages: list[ModelMessage] = Field(default_factory=list)


def write_json(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(value.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


class Store:
    def __init__(self, root: Path):
        self.root = root

    def identity(self) -> Identity:
        path = self.root / "identity.json"
        if path.exists():
            return Identity.model_validate_json(path.read_text(encoding="utf-8"))
        identity = Identity()
        write_json(path, identity)
        return identity

    def save(self, session: Session) -> None:
        write_json(self.root / "sessions" / f"{session.id}.json", session)

    def load(self, session_id: str) -> Session:
        path = self.root / "sessions" / f"{UUID(session_id)}.json"
        return Session.model_validate_json(path.read_text(encoding="utf-8"))

    def sessions(self) -> list[Session]:
        sessions = [
            Session.model_validate_json(path.read_text(encoding="utf-8"))
            for path in (self.root / "sessions").glob("*.json")
        ]
        return sorted(sessions, key=lambda session: session.updated_at, reverse=True)
