"""Account service: manage account ledger and positions for paper trading.

This module writes to PostgreSQL via existing engine helpers. If the configured
backend is not postgres/dualwrite, functions become no-ops.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import text

from config.runtime_config import get_account_initial_balance, get_account_system_config
from app.db import get_sqlalchemy_engine


def _paper_backend_enabled() -> bool:
    from config import runtime_config

    return runtime_config.get_database_backend() in {"postgres", "dualwrite"}


def _engine():
    if not _paper_backend_enabled():
        return None
    return get_sqlalchemy_engine()


def get_or_init_account(currency: str) -> dict[str, Any]:
    eng = _engine()
    now = datetime.now(timezone.utc)
    cur = str(currency).strip().upper()
    if eng is None:
        return {"account_id": cur, "balance": get_account_initial_balance(cur), "available": get_account_initial_balance(cur)}

    # Try to fetch latest ledger snapshot
    with eng.connect() as conn:
        r = conn.execute(
            text("SELECT balance, available, used_margin FROM account_ledger WHERE account_id = :aid ORDER BY snapshot_time DESC LIMIT 1"),
            {"aid": cur},
        ).first()
        if r:
            return {"account_id": cur, "balance": float(r[0] or 0.0), "available": float(r[1] or 0.0), "used_margin": float(r[2] or 0.0)}

        # initialize
        init_balance = get_account_initial_balance(cur)
        avail = float(init_balance)
        bal = float(init_balance)
        try:
            conn.execute(
                text(
                    "INSERT INTO account_ledger (account_id, balance, available, used_margin, unrealized_pnl, equity, snapshot_time, reason, meta) VALUES (:aid, :bal, :avail, 0, 0, :equity, CAST(:t AS timestamptz), 'init', '{}'::jsonb)"
                ),
                {"aid": cur, "bal": bal, "avail": avail, "equity": bal, "t": now.isoformat()},
            )
        except Exception as exc:
            logger.warning("[AccountService] init ledger failed account={} err={}", cur, str(exc))
        return {"account_id": cur, "balance": bal, "available": avail}


def get_available_balance(currency: str) -> float:
    ac = get_or_init_account(currency)
    return float(ac.get("available") or 0.0)


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

            # update ledger: increase used_margin, decrease available
            prev = conn.execute(
                text("SELECT balance, available, used_margin FROM account_ledger WHERE account_id = :aid ORDER BY snapshot_time DESC LIMIT 1"),
                {"aid": cur},
            ).first()
            if prev:
                prev_balance = float(prev[0] or 0.0)
                prev_avail = float(prev[1] or 0.0)
                prev_used = float(prev[2] or 0.0)
            else:
                prev_balance = get_account_initial_balance(cur)
                prev_avail = prev_balance
                prev_used = 0.0

            new_used = prev_used + entry_notional
            new_avail = prev_balance - new_used
            new_equity = prev_balance + 0.0
            conn.execute(
                text(
                    "INSERT INTO account_ledger (account_id, balance, available, used_margin, unrealized_pnl, equity, snapshot_time, reason, meta) VALUES (:aid, :bal, :avail, :used, 0, :equity, CAST(:t AS timestamptz), 'position_open', CAST(:meta AS jsonb))"
                ),
                {"aid": cur, "bal": prev_balance, "avail": new_avail, "used": new_used, "equity": new_equity, "t": now, "meta": f"{{\"linked_idea_id\": \"{idea_id}\", \"entry_notional\": {entry_notional}}}"},
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
            # find open position by linked_idea_id
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

            # update position
            conn.execute(
                text("UPDATE account_positions SET status='closed', exit_price=:exit_price, exit_notional=:exit_notional, realized_pnl=:pnl, realized_pnl_pct=:pnl_pct, closed_at=CAST(:t AS timestamptz), close_reason=:reason WHERE id = :pid"),
                {"exit_price": float(exit_price), "exit_notional": exit_notional, "pnl": realized_pnl, "pnl_pct": realized_pnl_pct, "t": now, "reason": close_reason, "pid": pos_id},
            )

            # update ledger: add realized_pnl to balance, reduce used_margin
            prev = conn.execute(
                text("SELECT balance, available, used_margin FROM account_ledger WHERE account_id = :aid ORDER BY snapshot_time DESC LIMIT 1"),
                {"aid": account_id},
            ).first()
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
            conn.execute(
                text("INSERT INTO account_ledger (account_id, balance, available, used_margin, unrealized_pnl, equity, snapshot_time, reason, meta) VALUES (:aid, :bal, :avail, :used, 0, :equity, CAST(:t AS timestamptz), 'position_close', CAST(:meta AS jsonb))"),
                {"aid": account_id, "bal": new_balance, "avail": new_avail, "used": new_used, "equity": new_balance, "t": now, "meta": f"{{\"linked_idea_id\": \"{idea_id}\", \"pnl\": {realized_pnl}}}"},
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
            # iterate open positions
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

            # write ledger snapshot per account
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
                conn.execute(text("INSERT INTO account_ledger (account_id, balance, available, used_margin, unrealized_pnl, equity, snapshot_time, reason, meta) VALUES (:aid, :bal, :avail, :used, :u, :equity, CAST(:t AS timestamptz), 'mark_to_market', CAST(:meta AS jsonb))"), {"aid": account_id, "bal": prev_balance, "avail": available, "used": prev_used, "u": unreal, "equity": equity, "t": now, "meta": "{}"})
    except Exception as exc:
        logger.error("[AccountService] mark_to_market failed error={}", str(exc))
