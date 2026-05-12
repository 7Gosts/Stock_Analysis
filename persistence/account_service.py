"""Account service: manage account ledger and positions for paper trading.

未配置 PostgreSQL DSN 或无法建连时，写入函数为 no-op。

显式资金变动：`deposit_funds` / `withdraw_funds` / `adjust_funds`（追加 `account_ledger` 快照，reason 分别为
`deposit` / `withdraw` / `adjustment`）。其它路径（开平仓、MTM）共用同一 INSERT SQL 片段。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import text

from config.runtime_config import get_account_initial_balance

from persistence.db import get_sqlalchemy_engine
from persistence.sql_loader import load_sql_text

_LEDGER_APPEND = load_sql_text("account/ledger_append_snapshot.sql")

_LATEST_LEDGER = text(
    "SELECT balance, available, used_margin, COALESCE(unrealized_pnl, 0) FROM account_ledger "
    "WHERE account_id = :aid ORDER BY snapshot_time DESC LIMIT 1"
)


def _paper_backend_enabled() -> bool:
    return get_sqlalchemy_engine() is not None


def _engine():
    if not _paper_backend_enabled():
        return None
    return get_sqlalchemy_engine()


def _ledger_append(
    conn: Any,
    *,
    aid: str,
    bal: float,
    avail: float,
    used: float,
    unreal: float,
    equity: float,
    t: str,
    reason: str,
    meta: dict[str, Any] | None,
) -> None:
    conn.execute(
        _LEDGER_APPEND,
        {
            "aid": aid,
            "bal": bal,
            "avail": avail,
            "used": used,
            "u": unreal,
            "equity": equity,
            "t": t,
            "reason": reason,
            "meta": json.dumps(meta or {}, ensure_ascii=False),
        },
    )


def _equity_snapshot(balance: float, used_margin: float, unrealized_pnl: float) -> float:
    return float(balance) - float(used_margin) + float(unrealized_pnl)


def get_or_init_account(currency: str) -> dict[str, Any]:
    """读 account_ledger 最新一行；无 PG 时退回 YAML 演示值。

    有 PG 且无账本行时**不再**自动 INSERT（首条 `reason=init` 由 Alembic `journal_004` 从 YAML 种子化）；
    此时返回 `ledger_missing=True`、可用余额 0，并由调用方打日志 / 拒绝开单。
    """
    eng = _engine()
    cur = str(currency).strip().upper()
    if eng is None:
        ib = get_account_initial_balance(cur)
        return {"account_id": cur, "balance": ib, "available": ib, "used_margin": 0.0}

    with eng.begin() as conn:
        r = conn.execute(_LATEST_LEDGER, {"aid": cur}).first()
        if r:
            return {
                "account_id": cur,
                "balance": float(r[0] or 0.0),
                "available": float(r[1] or 0.0),
                "used_margin": float(r[2] or 0.0),
                "ledger_missing": False,
            }

        logger.warning(
            "[AccountService] 账户 {} 在 account_ledger 中无快照；请先执行 `alembic upgrade head`（含 journal_004，"
            "会按 YAML accounts 写入 init 行）或手工插入 reason='init' 记录。开单将不使用数据库余额。",
            cur,
        )
        return {
            "account_id": cur,
            "balance": 0.0,
            "available": 0.0,
            "used_margin": 0.0,
            "ledger_missing": True,
        }


def ensure_accounts_initialized() -> None:
    """已弃用：PG 首条 init 由 Alembic `journal_004` 在升级时写入，勿依赖运行时循环初始化。"""
    logger.debug("[AccountService] ensure_accounts_initialized 为兼容保留，无操作（请使用 journal_004 种子）。")


def get_available_balance(currency: str) -> float:
    ac = get_or_init_account(currency)
    return float(ac.get("available") or 0.0)


def deposit_funds(currency: str, amount: float, *, note: str | None = None, now_utc: datetime | None = None) -> bool:
    """显式充值：增加 balance / available（不改变 used_margin），`reason='deposit'`。"""
    if amount <= 0:
        logger.warning("[AccountService] deposit_funds 跳过：amount 须为正，got={}", amount)
        return False
    return _apply_cash_delta(str(currency).strip().upper(), amount, reason="deposit", note=note, now_utc=now_utc)


def withdraw_funds(currency: str, amount: float, *, note: str | None = None, now_utc: datetime | None = None) -> bool:
    """显式提现：减少 balance / available（不改变 used_margin），`reason='withdraw'`。可用须足够。"""
    if amount <= 0:
        logger.warning("[AccountService] withdraw_funds 跳过：amount 须为正，got={}", amount)
        return False
    return _apply_cash_delta(str(currency).strip().upper(), -float(amount), reason="withdraw", note=note, now_utc=now_utc)


def adjust_funds(currency: str, delta: float, *, note: str | None = None, now_utc: datetime | None = None) -> bool:
    """显式调账：对 balance / available 同步增减 `delta`（可为负），`reason='adjustment'`。

    若调减后 `available` 为负会记日志但仍写入（管理纠偏场景）；提现请优先用 `withdraw_funds`。
    """
    if delta == 0:
        logger.warning("[AccountService] adjust_funds 跳过：delta 为 0")
        return False
    return _apply_cash_delta(str(currency).strip().upper(), float(delta), reason="adjustment", note=note, now_utc=now_utc)


def _apply_cash_delta(account_id: str, delta: float, *, reason: str, note: str | None, now_utc: datetime | None) -> bool:
    eng = _engine()
    if eng is None:
        logger.warning("[AccountService] {} 跳过：无 PostgreSQL 引擎", reason)
        return False
    now = (now_utc or datetime.now(timezone.utc)).isoformat()
    meta: dict[str, Any] = {"op": reason, "delta": delta}
    if note:
        meta["note"] = str(note)[:512]

    try:
        with eng.begin() as conn:
            prev = conn.execute(_LATEST_LEDGER, {"aid": account_id}).first()
            if not prev:
                logger.error(
                    "[AccountService] {} 失败：账户 {} 无 account_ledger 快照（请先 journal_004 或 init）。",
                    reason,
                    account_id,
                )
                return False
            pb = float(prev[0] or 0.0)
            pa = float(prev[1] or 0.0)
            pu = float(prev[2] or 0.0)
            pun = float(prev[3] or 0.0)

            if reason == "withdraw" and pa + delta < -1e-9:
                logger.error(
                    "[AccountService] withdraw 失败：账户 {} 可用 {:.8f} 不足提现 {:.8f}",
                    account_id,
                    pa,
                    -delta,
                )
                return False

            nb = pb + delta
            na = pa + delta
            if na < -1e-9 and reason == "adjustment":
                logger.warning(
                    "[AccountService] adjustment 后 available 为负 account={} new_avail={:.8f}",
                    account_id,
                    na,
                )
            eq = _equity_snapshot(nb, pu, pun)
            _ledger_append(
                conn,
                aid=account_id,
                bal=nb,
                avail=na,
                used=pu,
                unreal=pun,
                equity=eq,
                t=now,
                reason=reason,
                meta=meta,
            )
        logger.info("[AccountService] {} 成功 account={} delta={}", reason, account_id, delta)
        return True
    except Exception as exc:
        logger.exception("[AccountService] {} 失败 account={} err={}", reason, account_id, str(exc))
        return False


def open_position(currency: str, idea: dict[str, Any], fill_qty: float, fill_price: float, order_id: str, now_utc: datetime | None = None) -> None:
    eng = _engine()
    if eng is None:
        return
    now = (now_utc or datetime.now(timezone.utc)).isoformat()
    cur = str(currency).strip().upper()
    entry_notional = float(fill_qty) * float(fill_price)
    symbol = str(idea.get("symbol") or "")[:64]
    market = str(idea.get("market") or "")[:16]
    idea_id = str(idea.get("idea_id") or "")[:64]

    try:
        with eng.begin() as conn:
            prev = conn.execute(_LATEST_LEDGER, {"aid": cur}).first()
            if not prev:
                logger.error(
                    "[AccountService] open_position 中止：账户 {} 无 account_ledger 快照（请先 `alembic upgrade head` 含 journal_004）。",
                    cur,
                )
                return

            conn.execute(
                text(
                    "INSERT INTO account_positions (account_id, symbol, market, direction, status, qty, entry_price, entry_notional, opened_at, linked_order_id, linked_idea_id, meta) VALUES (:aid, :symbol, :market, :direction, 'open', :qty, :entry_price, :entry_notional, CAST(:t AS timestamptz), :oid, :iid, CAST(:meta AS jsonb))"
                ),
                {
                    "aid": cur,
                    "symbol": symbol,
                    "market": market,
                    "direction": str(idea.get("direction") or "long")[:16],
                    "qty": float(fill_qty),
                    "entry_price": float(fill_price),
                    "entry_notional": entry_notional,
                    "t": now,
                    "oid": order_id,
                    "iid": idea_id,
                    "meta": "{}",
                },
            )

            prev_balance = float(prev[0] or 0.0)
            prev_used = float(prev[2] or 0.0)
            pun = float(prev[3] or 0.0)

            new_used = prev_used + entry_notional
            new_avail = prev_balance - new_used
            new_equity = prev_balance + 0.0
            _ledger_append(
                conn,
                aid=cur,
                bal=prev_balance,
                avail=new_avail,
                used=new_used,
                unreal=0.0,
                equity=new_equity,
                t=now,
                reason="position_open",
                meta={"linked_idea_id": idea_id, "entry_notional": entry_notional},
            )
    except Exception as exc:
        logger.error("[AccountService] open_position failed account={} idea_id={} error={}", cur, idea_id, str(exc))


def close_position(idea_id: str, fill_qty: float, entry_price: float, exit_price: float, close_reason: str, now_utc: datetime | None = None) -> tuple[float, float]:
    """Close a position and update ledger; returns (realized_pnl, realized_pnl_pct)."""
    eng = _engine()
    if eng is None:
        return 0.0, 0.0
    now = (now_utc or datetime.now(timezone.utc)).isoformat()
    try:
        with eng.begin() as conn:
            pos = conn.execute(
                text("SELECT id, account_id, qty, entry_price, entry_notional FROM account_positions WHERE linked_idea_id = :iid AND status = 'open' ORDER BY opened_at LIMIT 1"),
                {"iid": idea_id},
            ).first()
            if not pos:
                logger.warning("[AccountService] close_position: no open position for idea_id={}", idea_id)
                return 0.0, 0.0
            pos_id = int(pos[0])
            account_id = str(pos[1])
            qty = float(pos[2] or 0.0)
            entry_notional = float(pos[4] or (qty * float(entry_price)))

            exit_notional = float(fill_qty) * float(exit_price)
            realized_pnl = exit_notional - entry_notional
            realized_pnl_pct = (realized_pnl / entry_notional) if entry_notional and entry_notional != 0 else 0.0

            conn.execute(
                text("UPDATE account_positions SET status='closed', exit_price=:exit_price, exit_notional=:exit_notional, realized_pnl=:pnl, realized_pnl_pct=:pnl_pct, closed_at=CAST(:t AS timestamptz), close_reason=:reason WHERE id = :pid"),
                {"exit_price": float(exit_price), "exit_notional": exit_notional, "pnl": realized_pnl, "pnl_pct": realized_pnl_pct, "t": now, "reason": close_reason, "pid": pos_id},
            )

            prev = conn.execute(_LATEST_LEDGER, {"aid": account_id}).first()
            if prev:
                prev_balance = float(prev[0] or 0.0)
                prev_avail = float(prev[1] or 0.0)
                prev_used = float(prev[2] or 0.0)
            else:
                prev_balance = get_account_initial_balance(account_id)
                prev_avail = prev_balance
                prev_used = 0.0

            new_balance = prev_balance + realized_pnl
            new_used = max(0.0, prev_used - entry_notional)
            new_avail = new_balance - new_used
            pun = 0.0
            eq = new_balance
            _ledger_append(
                conn,
                aid=account_id,
                bal=new_balance,
                avail=new_avail,
                used=new_used,
                unreal=pun,
                equity=eq,
                t=now,
                reason="position_close",
                meta={"linked_idea_id": idea_id, "pnl": realized_pnl},
            )
            return float(realized_pnl), float(realized_pnl_pct)
    except Exception as exc:
        logger.error("[AccountService] close_position failed idea_id={} error={}", idea_id, str(exc))
        return 0.0, 0.0


def mark_to_market(symbol_price_map: dict[str, float]) -> None:
    eng = _engine()
    if eng is None:
        return
    now = datetime.now(timezone.utc).isoformat()
    try:
        with eng.begin() as conn:
            rows = conn.execute(text("SELECT id, account_id, symbol, qty, entry_price FROM account_positions WHERE status = 'open'"))
            total_updates: dict[str, float] = {}
            for r in rows:
                pid = int(r[0])
                aid = str(r[1])
                symbol = str(r[2])
                qty = float(r[3] or 0.0)
                entry_price = float(r[4] or 0.0)
                cur_price = symbol_price_map.get(symbol)
                if cur_price is None:
                    continue
                unrealized = (cur_price - entry_price) * qty
                conn.execute(text("UPDATE account_positions SET unrealized_pnl = :u, meta = COALESCE(meta, '{}'::jsonb) WHERE id = :pid"), {"u": unrealized, "pid": pid})
                total_updates[aid] = total_updates.get(aid, 0.0) + unrealized

            for account_id, unreal in total_updates.items():
                prev = conn.execute(text("SELECT balance, used_margin FROM account_ledger WHERE account_id = :aid ORDER BY snapshot_time DESC LIMIT 1"), {"aid": account_id}).first()
                if prev:
                    prev_balance = float(prev[0] or 0.0)
                    prev_used = float(prev[1] or 0.0)
                else:
                    prev_balance = get_account_initial_balance(account_id)
                    prev_used = 0.0
                equity = prev_balance + unreal
                available = equity - prev_used
                _ledger_append(
                    conn,
                    aid=account_id,
                    bal=prev_balance,
                    avail=available,
                    used=prev_used,
                    unreal=unreal,
                    equity=equity,
                    t=now,
                    reason="mark_to_market",
                    meta={},
                )
    except Exception as exc:
        logger.error("[AccountService] mark_to_market failed error={}", str(exc))
