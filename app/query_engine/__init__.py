"""统一查询引擎：命名查询注册、执行与格式化。

AI 不直接生成任意 SQL，只允许调用注册表中的命名查询。
每个查询都配套参数校验（Pydantic）和结果格式器（→ CapabilityResult）。
"""
from app.query_engine.base import CapabilityResult, DomainType, IntentType
from app.query_engine.registry import SqlQuerySpec, QUERY_REGISTRY, get_query_spec
from app.query_engine.executor import execute_named_query

__all__ = [
    "CapabilityResult", "DomainType", "IntentType",
    "SqlQuerySpec", "QUERY_REGISTRY", "get_query_spec",
    "execute_named_query",
]