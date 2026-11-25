"""启动前自检：依赖、本地模型、索引版本和端口。

任一项不通过就列出原因和处理命令并中止启动，避免带着缺失的模型或旧索引运行。
"""

import importlib
import socket
import sqlite3
from contextlib import closing

PREPARE = "uv run --locked python -m backend.prepare"
REINDEX = "uv run --locked python -m backend.reindex"
SYNC = "uv sync --locked"
PACKAGES = ("fastembed", "rapidocr", "rapid_layout", "rapid_table")
HOST, PORT = "127.0.0.1", 8000
LISTED = 10  # 索引过期时最多列出的文件数


def check_packages():
    missing = []
    for name in PACKAGES:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    if missing:
        return [f"缺少依赖：{'、'.join(missing)}。请先停止正在运行的后端，再执行 {SYNC}。"]
    return []


def check_models(cache_dir):
    """嵌入模型与 OCR、版面、表格模型都须在本地且校验通过。"""
    from .embeddings import local_model_path
    from .parsing.ocr import OCRUnavailable, model_paths

    problems = []
    try:
        local_model_path(cache_dir)
    except RuntimeError as exc:
        problems.append(str(exc))
    try:
        model_paths(cache_dir)
    except OCRUnavailable:
        problems.append(f"OCR、版面或表格识别模型未准备或校验失败，请先运行 {PREPARE}")
    return problems


def stale_indexes(database):
    """运行库中没有索引或索引版本与当前程序不一致的文件：[(标题, 现有版本或 None)]。"""
    from .indexing import INDEX_VERSION

    if not database.exists():
        return []
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as db:
        tables = {name for name, in db.execute("select name from sqlite_master where type='table'")}
        if "documents" not in tables:
            return []
        if "document_indexes" not in tables:
            return [(title, None) for title, in db.execute("select title from documents")]
        rows = db.execute("select d.title, i.version from documents d "
                          "left join document_indexes i on i.document_id = d.id").fetchall()
    return [(title, version) for title, version in rows if version != INDEX_VERSION]


def stale_message(stale):
    from .indexing import INDEX_VERSION

    lines = [f"有 {len(stale)} 份制度的检索索引与当前程序的索引版本（{INDEX_VERSION}）不一致："]
    lines += [f"  - {title}（{version or '没有索引'}）" for title, version in stale[:LISTED]]
    if len(stale) > LISTED:
        lines.append(f"  - 另有 {len(stale) - LISTED} 份")
    lines.append(f"请先停止后端，运行 {REINDEX} 用库中保存的原 PDF 重建索引"
                 "（文档、账号、会话和经验保留），完成后再启动。")
    return "\n".join(lines)


def check_index(data_dir):
    stale = stale_indexes(data_dir / "banshi.sqlite3")
    return [stale_message(stale)] if stale else []


def check_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, PORT))
        except OSError:
            return [(f"端口 {PORT} 已被占用，可能已有后端在运行。在原终端按 Ctrl+C 停止它；"
                     f"不确定是哪个进程时用 netstat -ano | findstr :{PORT} 查看。")]
    return []


def run(settings):
    """返回全部不通过项的说明；依赖缺失时其余检查无法进行，直接返回。"""
    problems = check_packages()
    if problems:
        return problems
    return check_models(settings.model_cache_dir) + check_index(settings.data_dir) + check_port()
