"""从仓库 `sql/` 目录加载 SQL 文本，供运行时 `sqlalchemy.text()` 使用。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause

_SQL_ROOT = Path(__file__).resolve().parent.parent / "sql"


def _resolve_sql_path(relative_path: str) -> Path:
    rel = relative_path.strip().replace("\\", "/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        raise ValueError(f"非法 SQL 路径: {relative_path!r}")
    return _SQL_ROOT.joinpath(*rel.split("/"))


@lru_cache(maxsize=64)
def load_sql(relative_path: str) -> str:
    """读取 `sql/<relative_path>` 全文（UTF-8），带进程内缓存。

    参数示例：`journal/idea_upsert.sql`
    """
    path = _resolve_sql_path(relative_path)
    if not path.is_file():
        raise FileNotFoundError(f"SQL 文件不存在: {path}")
    return path.read_text(encoding="utf-8")


def load_sql_text(relative_path: str) -> TextClause:
    """`text(load_sql(...).strip())`，供 `conn.execute(...)` 直接使用。"""
    return text(load_sql(relative_path).strip())
