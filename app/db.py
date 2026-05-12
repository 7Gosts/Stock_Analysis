"""PostgreSQL 连接池（仅当 database.backend 非纯 jsonl 时创建）。"""
from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

_engine: Engine | None = None


def get_sqlalchemy_engine() -> Engine | None:
    """返回全局 Engine；backend=jsonl 时返回 None。"""
    global _engine
    from config.runtime_config import get_database_backend, get_postgres_dsn

    backend = get_database_backend()
    if backend == "jsonl":
        return None
    dsn = get_postgres_dsn()
    if not dsn:
        return None
    if _engine is None:
        from config.runtime_config import get_database_config

        db = get_database_config()
        pg = db.get("postgres") if isinstance(db.get("postgres"), dict) else {}
        pool_size = int(pg.get("pool_size", 5))
        max_overflow = int(pg.get("max_overflow", 10))
        pool_pre_ping = bool(pg.get("pool_pre_ping", True))
        pool_recycle = int(pg.get("pool_recycle", 1800))
        echo = bool(pg.get("echo", False))
        _engine = create_engine(
            dsn,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=pool_pre_ping,
            pool_recycle=pool_recycle,
            echo=echo,
            future=True,
        )
    return _engine


def reset_engine_for_tests() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None
