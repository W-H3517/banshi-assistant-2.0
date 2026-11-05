import json
import logging
from datetime import datetime

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from pydantic_ai.models.instrumented import InstrumentationSettings


class RedactingFormatter(logging.Formatter):
    def __init__(self, secret):
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")
        self.secret = secret

    def format(self, record):
        text = super().format(record)
        return text.replace(self.secret, "[REDACTED]") if self.secret else text


class TraceLogStream:
    """仅把官方导出器的文本接入已有日志，不自行采集模型或工具事件。"""

    def write(self, text):
        logging.getLogger("backend.trace").info("\n%s", text.rstrip())
        return len(text)

    def flush(self):
        pass


def readable_span(span):
    # 官方格式中的消息属性是 JSON 字符串，展开后中文与多轮消息更容易阅读。
    record = json.loads(span.to_json())
    for key, value in record["attributes"].items():
        if isinstance(value, str) and value.startswith(("[", "{")):
            try:
                record["attributes"][key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return json.dumps(record, ensure_ascii=False, indent=2) + "\n"


class LoggingRuntime:
    def __init__(self, settings):
        directory = settings.data_dir / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"server-{datetime.now():%Y%m%d-%H%M%S-%f}-{settings.log_mode}.txt"
        self.handlers = [logging.StreamHandler(), logging.FileHandler(self.path, encoding="utf-8")]
        self.root = logging.getLogger()
        self.old_level = self.root.level
        self.root.setLevel(logging.INFO)
        for handler in self.handlers:
            handler.setFormatter(RedactingFormatter(settings.api_key))
            self.root.addHandler(handler)
        # 详细内容由框架追踪提供，不启用可能包含认证头的 HTTP DEBUG 日志。
        self.old_levels = {}
        for name in ("openai", "httpcore", "httpcore2", "aiosqlite"):
            logger = logging.getLogger(name)
            self.old_levels[name] = logger.level
            logger.setLevel(logging.WARNING)
        self.provider = None
        self.instrumentation = None
        if settings.log_mode == "detailed":
            self.provider = TracerProvider(resource=Resource.create({
                "service.name": "banshi-assistant",
            }), shutdown_on_exit=False)
            self.provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter(
                out=TraceLogStream(), formatter=readable_span,
            )))
            self.instrumentation = InstrumentationSettings(
                tracer_provider=self.provider, include_content=True, include_binary_content=False,
            )
        logging.getLogger(__name__).info("log_mode=%s log_file=%s", settings.log_mode, self.path)

    def close(self):
        if self.provider:
            self.provider.shutdown()
        for handler in self.handlers:
            self.root.removeHandler(handler)
            handler.close()
        self.root.setLevel(self.old_level)
        for name, level in self.old_levels.items():
            logging.getLogger(name).setLevel(level)
