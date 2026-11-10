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
    provider: Literal["deepseek", "openai"] = "deepseek"
    openai_api: Literal["responses", "chat_completions"] = "responses"
    reasoning_effort: str | None = None

    @property
    def conversation_model(self):
        if self.provider == "deepseek":
            return self.model
        return f"openai:{self.openai_api}:{self.model}"

    @classmethod
    def load(cls):
        values = dotenv_values(ROOT / ".env", encoding="utf-8-sig")
        log_mode = os.environ.get("BANSHI_LOG_MODE", "simple")
        if log_mode not in ("simple", "detailed"):
            raise ValueError("BANSHI_LOG_MODE 必须为 simple 或 detailed")
        def value(name, default=""):
            return os.getenv(name, values.get(name) or default).strip()

        provider = value("MODEL_PROVIDER", "deepseek")
        if provider not in ("deepseek", "openai"):
            raise ValueError("MODEL_PROVIDER 必须为 deepseek 或 openai")
        model = value("OPENAI_MODEL") if provider == "openai" else value("DEEPSEEK_MODEL", "deepseek-flash")
        if not model:
            raise ValueError("请设置OPENAI_MODEL为本次要测试且账号可用的模型")
        openai_api = value("OPENAI_API", "responses")
        effort = value("OPENAI_REASONING_EFFORT") or None
        if provider == "openai":
            if openai_api not in ("responses", "chat_completions"):
                raise ValueError("OPENAI_API 必须为 responses 或 chat_completions")
            if effort not in (None, "none", "minimal", "low", "medium", "high", "xhigh", "max"):
                raise ValueError("OPENAI_REASONING_EFFORT 不是有效的推理级别")
        return cls(
            api_key=value("OPENAI_API_KEY" if provider == "openai" else "DEEPSEEK_API_KEY"),
            model=model, provider=provider, openai_api=openai_api, reasoning_effort=effort,
            log_mode=log_mode,
        )
