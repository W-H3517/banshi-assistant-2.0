import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    data_dir: Path = ROOT / "data/app"
    api_key: str = field(default="", repr=False)
    model: str = "deepseek-flash"

    @classmethod
    def load(cls):
        values = dotenv_values(ROOT / ".env", encoding="utf-8-sig")
        return cls(
            api_key=os.getenv("DEEPSEEK_API_KEY", values.get("DEEPSEEK_API_KEY") or "").strip(),
            model=os.getenv("DEEPSEEK_MODEL", values.get("DEEPSEEK_MODEL") or "deepseek-flash"),
        )
