"""命名查询执行器。

通过 persistence.db.get_sqlalchemy_engine() 和 sql_loader 执行 SQL，
返回 list[dict] 给 formatter 处理。
"""
from __future__ import annotations

from typing import Any

from persistence.db import get_sqlalchemy_engine
from persistence.sql_loader import load_sql_text

from app.query_engine.registry import get_query_spec, SqlQuerySpec


def execute_named_query(name: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """执行命名查询，返回行数据列表。

    Args:
        name: 注册表中的查询名，如 "account.latest_balances"
        params: 查询参数字典，会先通过 params_schema 校验

    Returns:
        list[dict]: 查询结果行，每行是列名→值的字典

    Raises:
        ValueError: 查询名未注册
        RuntimeError: 数据库不可用
    """
    spec = get_query_spec(name)

    # 校验参数
    raw_params = params or {}
    validated = spec.params_schema(**raw_params)
    bind_params = validated.model_dump()

    engine = get_sqlalchemy_engine()
    if engine is None:
        raise RuntimeError("PostgreSQL 数据库不可用，无法执行命名查询")

    sql_text = load_sql_text(spec.sql_path)

    with engine.connect() as conn:
        result = conn.execute(sql_text, bind_params)
        rows = [row._mapping for row in result]

    # 转为普通 dict（RowMapping 不是标准 dict）
    return [dict(r) for r in rows]