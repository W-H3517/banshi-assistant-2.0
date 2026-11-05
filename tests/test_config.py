import pytest

from agent_demo.config import Config


def test_missing_key_fails_without_echoing_contents(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        Config.load(tmp_path)


def test_utf8_bom_config_and_secret_repr(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=test-secret\n", encoding="utf-8-sig")
    config = Config.load(tmp_path)
    assert config.api_key == "test-secret"
    assert config.model == "deepseek-flash"
    assert "test-secret" not in repr(config)
