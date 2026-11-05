import logging
import sys
from datetime import datetime
from pathlib import Path


class RedactKey(logging.Filter):
    def __init__(self, key: str):
        super().__init__()
        self.key = key

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = record.getMessage().replace(self.key, "[REDACTED]")
        record.args = ()
        return True


def setup_logging(directory: Path, key: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"agent-{datetime.now():%Y%m%d-%H%M%S-%f}.txt"
    handlers = [logging.StreamHandler(sys.stderr), logging.FileHandler(path, encoding="utf-8")]
    for handler in handlers:
        handler.addFilter(RedactKey(key))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )
    # SDK 的 INFO 请求日志可保留；不启用含完整请求体的 DEBUG。
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    return path
