import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    data_dir: Path = ROOT / "data/app"
    api_key: str = field(default="", repr=False)
    model: str = "deepseek-flash"
    log_mode: Literal["simple", "detailed"] = "simple"
    model_cache_dir: Path = ROOT / "data/models"

    @classmethod
    def load(cls):
        values = dotenv_values(ROOT / ".env", encoding="utf-8-sig")
        log_mode = os.environ.get("BANSHI_LOG_MODE", "simple")
        if log_mode not in ("simple", "detailed"):
            raise ValueError("BANSHI_LOG_MODE 必须为 simple 或 detailed")
        return cls(
            api_key=os.getenv("DEEPSEEK_API_KEY", values.get("DEEPSEEK_API_KEY") or "").strip(),
            model=os.getenv("DEEPSEEK_MODEL", values.get("DEEPSEEK_MODEL") or "deepseek-flash"),
            log_mode=log_mode,
        )
