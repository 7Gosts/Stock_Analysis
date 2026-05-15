"""命名查询注册表。

每个命名查询配套 SQL 路径、参数 schema 和结果 formatter，
AI 不直接生成任意 SQL，只允许调用注册表中的命名查询。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from app.query_engine.base import CapabilityResult


class LatestBalancesParams(BaseModel):
    account_id: str = ""


class OpenPositionsParams(BaseModel):
    account_id: str = ""
    symbol: str = ""
    limit: int = 50


class ActiveIdeasParams(BaseModel):
    symbol: str = ""
    limit: int = 30


class RecentOrdersParams(BaseModel):
    symbol: str = ""
    limit: int = 20


class RecentFillsParams(BaseModel):
    symbol: str = ""
    limit: int = 20


class OrderHealthParams(BaseModel):
    symbol: str = ""


@dataclass(frozen=True)
class SqlQuerySpec:
    name: str
    sql_path: str
    params_schema: type[BaseModel]
    formatter: Callable[[list[dict[str, Any]], dict[str, Any]], CapabilityResult]


# ── Formatter 函数 ──────────────────────────────────────


def _fmt_latest_balances(rows: list[dict[str, Any]], params: dict[str, Any]) -> CapabilityResult:
    if not rows:
        return CapabilityResult(
            domain="sim_account", intent="overview",
            summary="暂无账户余额数据。",
            metrics={},
        )
    metrics = {}
    for r in rows:
        aid = r.get("account_id", "?")
        metrics[aid] = {
            "balance": r.get("balance", 0),
            "available": r.get("available", 0),
            "used_margin": r.get("used_margin", 0),
            "unrealized_pnl": r.get("unrealized_pnl", 0),
            "equity": r.get("equity", 0),
            "snapshot_time": str(r.get("snapshot_time", "")),
            "reason": r.get("reason", ""),
        }
    lines = [f"{aid}: 余额 {m['balance']}, 可用 {m['available']}, 权益 {m['equity']}" for aid, m in metrics.items()]
    summary = "账户余额：\n" + "\n".join(lines)
    return CapabilityResult(
        domain="sim_account", intent="overview",
        summary=summary, tables=rows, metrics=metrics,
        evidence_sources=["account_ledger"],
    )


def _fmt_open_positions(rows: list[dict[str, Any]], params: dict[str, Any]) -> CapabilityResult:
    if not rows:
        return CapabilityResult(
            domain="sim_account", intent="positions",
            summary="当前无未平仓持仓。",
            metrics={"open_positions": 0},
        )
    metrics = {"open_positions": len(rows)}
    lines = [f"{r.get('symbol', '?')} qty={r.get('qty', 0)} entry={r.get('entry_price', 0)} uPnL={r.get('unrealized_pnl', 0)}" for r in rows]
    summary = f"未平仓持仓 {len(rows)} 笔：\n" + "\n".join(lines)
    return CapabilityResult(
        domain="sim_account", intent="positions",
        summary=summary, tables=rows, metrics=metrics,
        evidence_sources=["account_positions"],
    )


def _fmt_active_ideas(rows: list[dict[str, Any]], params: dict[str, Any]) -> CapabilityResult:
    if not rows:
        return CapabilityResult(
            domain="sim_account", intent="active_ideas",
            summary="当前无活动交易想法。",
            metrics={"active_ideas": 0},
        )
    by_status: dict[str, int] = {}
    for r in rows:
        s = r.get("status", "?")
        by_status[s] = by_status.get(s, 0) + 1
    metrics = {"active_ideas": len(rows), "by_status": by_status}
    lines = [f"[{r.get('status', '?')}] {r.get('symbol', '?')} {r.get('direction', '?')} idea={r.get('idea_id', '?')}" for r in rows]
    summary = f"活动想法 {len(rows)} 条（{by_status}）：\n" + "\n".join(lines)
    return CapabilityResult(
        domain="sim_account", intent="active_ideas",
        summary=summary, tables=rows, metrics=metrics,
        evidence_sources=["journal_ideas"],
    )


def _fmt_recent_orders(rows: list[dict[str, Any]], params: dict[str, Any]) -> CapabilityResult:
    if not rows:
        return CapabilityResult(
            domain="sim_account", intent="orders",
            summary="暂无委托记录。",
            metrics={"total_orders": 0},
        )
    metrics = {"total_orders": len(rows)}
    lines = [f"{r.get('symbol', '?')} {r.get('side', '?')} status={r.get('status', '?')} qty={r.get('requested_qty', 0)}" for r in rows]
    summary = f"最近 {len(rows)} 条委托：\n" + "\n".join(lines)
    return CapabilityResult(
        domain="sim_account", intent="orders",
        summary=summary, tables=rows, metrics=metrics,
        evidence_sources=["paper_orders"],
    )


def _fmt_recent_fills(rows: list[dict[str, Any]], params: dict[str, Any]) -> CapabilityResult:
    if not rows:
        return CapabilityResult(
            domain="sim_account", intent="fills",
            summary="暂无成交记录。",
            metrics={"total_fills": 0},
        )
    metrics = {"total_fills": len(rows)}
    lines = [f"{r.get('symbol', '?')} {r.get('side', '?')} qty={r.get('fill_qty', 0)} price={r.get('fill_price', 0)}" for r in rows]
    summary = f"最近 {len(rows)} 笔成交：\n" + "\n".join(lines)
    return CapabilityResult(
        domain="sim_account", intent="fills",
        summary=summary, tables=rows, metrics=metrics,
        evidence_sources=["paper_fills"],
    )


def _fmt_order_health(rows: list[dict[str, Any]], params: dict[str, Any]) -> CapabilityResult:
    if not rows:
        return CapabilityResult(
            domain="sim_account", intent="health",
            summary="对账统计数据不可用。",
            metrics={},
        )
    r = rows[0]
    metrics = {
        "pending_orders": r.get("pending_orders", 0),
        "filled_orders": r.get("filled_orders", 0),
        "total_fills": r.get("total_fills", 0),
        "active_ideas": r.get("active_ideas", 0),
        "open_positions": r.get("open_positions", 0),
    }
    summary = (
        f"对账状态：pending_orders={metrics['pending_orders']}, "
        f"filled_orders={metrics['filled_orders']}, "
        f"total_fills={metrics['total_fills']}, "
        f"active_ideas={metrics['active_ideas']}, "
        f"open_positions={metrics['open_positions']}"
    )
    return CapabilityResult(
        domain="sim_account", intent="health",
        summary=summary, tables=rows, metrics=metrics,
        evidence_sources=["paper_orders", "paper_fills", "journal_ideas", "account_positions"],
    )


# ── 注册表 ──────────────────────────────────────────────

QUERY_REGISTRY: dict[str, SqlQuerySpec] = {
    "account.latest_balances": SqlQuerySpec(
        name="account.latest_balances",
        sql_path="queries/account/latest_balances.sql",
        params_schema=LatestBalancesParams,
        formatter=_fmt_latest_balances,
    ),
    "account.open_positions": SqlQuerySpec(
        name="account.open_positions",
        sql_path="queries/account/open_positions.sql",
        params_schema=OpenPositionsParams,
        formatter=_fmt_open_positions,
    ),
    "account.active_ideas": SqlQuerySpec(
        name="account.active_ideas",
        sql_path="queries/account/active_ideas.sql",
        params_schema=ActiveIdeasParams,
        formatter=_fmt_active_ideas,
    ),
    "account.recent_orders": SqlQuerySpec(
        name="account.recent_orders",
        sql_path="queries/account/recent_orders.sql",
        params_schema=RecentOrdersParams,
        formatter=_fmt_recent_orders,
    ),
    "account.recent_fills": SqlQuerySpec(
        name="account.recent_fills",
        sql_path="queries/account/recent_fills.sql",
        params_schema=RecentFillsParams,
        formatter=_fmt_recent_fills,
    ),
    "account.order_health": SqlQuerySpec(
        name="account.order_health",
        sql_path="queries/account/order_health.sql",
        params_schema=OrderHealthParams,
        formatter=_fmt_order_health,
    ),
}


def get_query_spec(name: str) -> SqlQuerySpec:
    spec = QUERY_REGISTRY.get(name)
    if spec is None:
        raise ValueError(f"未注册的命名查询: {name}")
    return spec