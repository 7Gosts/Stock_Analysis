"""模拟账户能力层：查看资金、持仓、挂单、成交与一致性状态。

这是 sim_account 域的 capability 实现，与 market / research 同级。
通过 query_engine 执行命名查询，产出 CapabilityResult。
"""
from __future__ import annotations

from typing import Any, Literal

from app.query_engine.base import CapabilityResult
from app.query_engine.executor import execute_named_query


SimAccountScope = Literal[
    "overview", "positions", "active_ideas", "orders", "fills", "health",
]

# overview 组合查询：一次调用返回余额 + 持仓 + 活动想法 + 对账统计
_OVERVIEW_QUERIES = [
    ("account.latest_balances", {}),
    ("account.open_positions", {"limit": 50}),
    ("account.active_ideas", {"limit": 30}),
    ("account.order_health", {}),
]

# scope → 单查询映射
_SCOPE_QUERIES: dict[SimAccountScope, tuple[str, dict[str, Any]]] = {
    "positions": ("account.open_positions", {"limit": 50}),
    "active_ideas": ("account.active_ideas", {"limit": 30}),
    "orders": ("account.recent_orders", {"limit": 20}),
    "fills": ("account.recent_fills", {"limit": 20}),
    "health": ("account.order_health", {}),
}


def view_sim_account_state(
    *,
    scope: SimAccountScope = "overview",
    account_id: str | None = None,
    symbol: str | None = None,
    limit: int = 20,
) -> CapabilityResult:
    """查看模拟账户状态，返回 CapabilityResult。

    Args:
        scope: 查询范围
            - overview: 余额 + 持仓 + 活动想法 + 对账统计（组合）
            - positions: 当前未平仓持仓
            - active_ideas: watch/pending/filled 的活动交易想法
            - orders: 最近委托
            - fills: 最近成交
            - health: order/fill 对账统计
        account_id: 可选，指定账户 ID
        symbol: 可选，指定标的
        limit: 返回条数上限

    Returns:
        CapabilityResult: 统一结果结构，summary 可直接作为用户回复
    """
    common_params: dict[str, Any] = {
        "account_id": account_id or "",
        "symbol": symbol or "",
        "limit": limit,
    }

    if scope == "overview":
        return _query_overview(common_params)

    query_name, default_params = _SCOPE_QUERIES[scope]
    spec, params = _prepare_query(query_name, default_params=default_params, common_params=common_params)
    rows = execute_named_query(query_name, params)
    return spec.formatter(rows, params)


def _query_overview(common_params: dict[str, Any]) -> CapabilityResult:
    """overview 组合查询，合并多个子结果为一条 CapabilityResult。"""
    sub_results: list[CapabilityResult] = []

    for query_name, base_params in _OVERVIEW_QUERIES:
        spec, params = _prepare_query(query_name, default_params=base_params, common_params=common_params)
        try:
            rows = execute_named_query(query_name, params)
            sub_results.append(spec.formatter(rows, params))
        except Exception:
            # 某个子查询失败不阻塞整体 overview
            sub_results.append(CapabilityResult(
                domain="sim_account", intent="overview",
                summary=f"[{query_name}] 查询暂不可用",
            ))

    # 合并 summaries
    summaries = [r.summary for r in sub_results if r.summary]
    combined_summary = "\n\n".join(summaries) if summaries else "模拟账户数据暂不可用。"

    # 合并 metrics
    combined_metrics: dict[str, Any] = {}
    for r in sub_results:
        combined_metrics.update(r.metrics)

    # 合并 evidence_sources
    combined_sources: list[str] = []
    for r in sub_results:
        combined_sources.extend(r.evidence_sources)
    combined_sources = list(dict.fromkeys(combined_sources))

    # 合并 tables
    combined_tables: list[dict[str, Any]] = []
    for r in sub_results:
        combined_tables.extend(r.tables)

    display_prefs: dict[str, Any] = {}
    for r in sub_results:
        if r.default_display_prefs:
            display_prefs.update(r.default_display_prefs)

    return CapabilityResult(
        domain="sim_account",
        intent="overview",
        summary=combined_summary,
        tables=combined_tables,
        metrics=combined_metrics,
        evidence_sources=combined_sources,
        meta={"sub_queries": [q[0] for q in _OVERVIEW_QUERIES]},
        default_display_prefs=display_prefs or None,
    )


def _get_spec(query_name: str):
    from app.query_engine.registry import get_query_spec
    return get_query_spec(query_name)


def _prepare_query(
    query_name: str,
    *,
    default_params: dict[str, Any],
    common_params: dict[str, Any],
):
    spec = _get_spec(query_name)
    merged = {**default_params, **common_params}
    allowed_fields = getattr(spec.params_schema, "model_fields", {})
    params = {key: merged[key] for key in allowed_fields if key in merged}
    return spec, params