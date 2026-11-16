import os
import subprocess
import sys

import pytest
from uvicorn.importer import import_from_string

from backend.__main__ import main
from backend.config import ROOT


@pytest.mark.parametrize("mode", ["simple", "detailed"])
def test_reload_uses_importable_factory_and_inherits_log_mode(monkeypatch, mode):
    monkeypatch.setenv("BANSHI_LOG_MODE", "simple")
    monkeypatch.setattr(sys, "argv", ["backend", "--reload", "--log-mode", mode])
    invocation = {}

    def capture(app, **kwargs):
        invocation.update(app=app, **kwargs)

    monkeypatch.setattr("backend.__main__.uvicorn.run", capture)
    main()
    assert callable(import_from_string(invocation["app"]))
    assert invocation["factory"] is True
    assert invocation["reload"] is True
    assert invocation["reload_dirs"] == [str(ROOT / "backend")]
    # 使用新 Python 进程加载配置，验证重载子进程不会丢失命令行指定的日志模式。
    result = subprocess.check_output([
        sys.executable, "-c",
        "from backend.config import Settings; print(Settings.load().log_mode)",
    ], cwd=ROOT, env=os.environ.copy(), text=True)
    assert result.strip() == mode
