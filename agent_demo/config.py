import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Config:
    api_key: str = field(repr=False)
    model: str = "deepseek-flash"

    @classmethod
    def load(cls, root: Path = ROOT) -> "Config":
        values = dotenv_values(root / ".env", encoding="utf-8-sig")
        key = os.environ.get("DEEPSEEK_API_KEY", values.get("DEEPSEEK_API_KEY") or "").strip()
        if not key:
            raise ValueError("请在项目 .env 中填写 DEEPSEEK_API_KEY。")
        model = os.environ.get("DEEPSEEK_MODEL", values.get("DEEPSEEK_MODEL") or "deepseek-flash")
        return cls(api_key=key, model=model.strip())
