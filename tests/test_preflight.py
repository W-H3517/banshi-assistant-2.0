import socket
import sqlite3

from backend import preflight
from backend.config import Settings
from backend.indexing import INDEX_VERSION


def database(path, rows):
    with sqlite3.connect(path) as db:
        db.execute("create table documents (id text, title text)")
        db.execute("create table document_indexes (document_id text, version text)")
        for number, (title, version) in enumerate(rows):
            db.execute("insert into documents values (?, ?)", (str(number), title))
            if version:
                db.execute("insert into document_indexes values (?, ?)", (str(number), version))


def test_stale_indexes_list_files_and_reindex_command(tmp_path):
    database(tmp_path / "banshi.sqlite3", [("现行办法", INDEX_VERSION), ("旧办法", "heading-body-v5"),
                                           ("未建索引", None)])
    problems = preflight.check_index(tmp_path)
    assert len(problems) == 1
    assert "旧办法（heading-body-v5）" in problems[0] and "未建索引（没有索引）" in problems[0]
    assert "现行办法" not in problems[0] and preflight.REINDEX in problems[0]


def test_current_or_missing_database_passes(tmp_path):
    assert preflight.check_index(tmp_path) == []
    database(tmp_path / "banshi.sqlite3", [("现行办法", INDEX_VERSION)])
    assert preflight.check_index(tmp_path) == []


def test_missing_models_and_packages_and_busy_port(tmp_path, monkeypatch):
    problems = preflight.check_models(tmp_path)
    assert len(problems) == 2 and all(preflight.PREPARE in p for p in problems)
    monkeypatch.setattr(preflight, "PACKAGES", ("fastembed", "no_such_package_for_test"))
    problems = preflight.run(Settings(data_dir=tmp_path, model_cache_dir=tmp_path))
    assert problems == [f"缺少依赖：no_such_package_for_test。请先停止正在运行的后端，再执行 {preflight.SYNC}。"]
    with socket.socket() as busy:
        busy.bind((preflight.HOST, 0))
        busy.listen()
        monkeypatch.setattr(preflight, "PORT", busy.getsockname()[1])
        assert "已被占用" in preflight.check_port()[0]


def test_prepared_environment_passes():
    # 本机已准备模型；端口可能被正在运行的后端占用，不在此断言。
    assert preflight.check_packages() == [] and preflight.check_models(Settings().model_cache_dir) == []
