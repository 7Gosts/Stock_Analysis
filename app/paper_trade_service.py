"""模拟委托 / 成交（仅 PostgreSQL；jsonl backend 为 no-op）。"""
from __future__ import annotations

import hashlib
import json
import traceback
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db import get_sqlalchemy_engine
from analysis.trade_journal import parse_iso_utc


def _paper_backend_enabled() -> bool:
    from config import runtime_config

    return runtime_config.get_database_backend() in {"postgres", "dualwrite"}


def _engine() -> Engine | None:
    if not _paper_backend_enabled():
        return None
    return get_sqlalchemy_engine()


def _idea_hash32(idea_id: str) -> str:
    return hashlib.sha256(idea_id.encode("utf-8")).hexdigest()[:32]


def stable_order_id(idea_id: str) -> str:
    return f"po_{_idea_hash32(idea_id)}"


def stable_fill_id(idea_id: str, fill_seq: int) -> str:
    return f"pf{fill_seq}_{_idea_hash32(idea_id)}"


def has_entry_fill_for_idea(idea_id: str) -> bool:
    eng = _engine()
    if eng is None or not idea_id:
        return False
    with eng.connect() as conn:
        r = conn.execute(
            text("SELECT 1 FROM paper_fills WHERE idea_id = :iid AND fill_seq = 1 LIMIT 1"),
            {"iid": idea_id},
        ).first()
        return r is not None


def has_exit_fill_for_idea(idea_id: str) -> bool:
    eng = _engine()
    if eng is None or not idea_id:
        return False
    with eng.connect() as conn:
        r = conn.execute(
            text("SELECT 1 FROM paper_fills WHERE idea_id = :iid AND fill_seq = 2 LIMIT 1"),
            {"iid": idea_id},
        ).first()
        return r is not None


def _parse_fill_time(ts: str | None, *, fallback: datetime) -> datetime:
    dt = parse_iso_utc(ts) if ts else None
    return dt if dt is not None else fallback


def _side_from_direction(direction: str) -> str:
    return "buy" if str(direction or "").lower() != "short" else "sell"


def _jb(obj: Any) -> str:
    return json.dumps(obj if obj is not None else {}, ensure_ascii=False)


def create_entry_order_and_fill(idea: dict[str, Any], *, now_utc: datetime | None = None) -> None:
    """watch/pending -> filled：插入 1 条 paper_orders + 1 条 entry fill（fill_seq=1）。幂等。"""
    eng = _engine()
    if eng is None:
        return
    now = now_utc or datetime.now(timezone.utc)
    idea_id = str(idea.get("idea_id") or "").strip()
    if not idea_id:
        return
    if has_entry_fill_for_idea(idea_id):
        return

    order_id = stable_order_id(idea_id)
    fill_id = stable_fill_id(idea_id, 1)
    symbol = str(idea.get("symbol") or "")
    market = str(idea.get("market") or "UNK")
    provider = str(idea.get("provider") or "tickflow")
    interval = str(idea.get("interval") or "1d")
    direction = str(idea.get("direction") or "long")
    side = _side_from_direction(direction)
    zone = idea.get("entry_zone")
    limit_px = idea.get("entry_price")
    if limit_px is None and isinstance(zone, list) and len(zone) == 2:
        limit_px = (float(zone[0]) + float(zone[1])) / 2.0
    stop_px = idea.get("stop_loss")
    fill_px = idea.get("fill_price")
    if fill_px is None and limit_px is not None:
        fill_px = float(limit_px)
    if fill_px is None:
        logger.warning("[PaperTrade] skip entry fill: no fill_price idea_id={}", idea_id)
        return
    fill_time = _parse_fill_time(str(idea.get("filled_at_utc") or ""), fallback=now)
    simulation_rule = {"engine": "paper_bar_touch", "note": "phase1_single_fill"}

    ins_order = text(
        """
        INSERT INTO paper_orders (
          order_id, idea_id, symbol, market, provider, interval,
          side, order_type, tif,
          requested_qty, requested_notional,
          limit_price, trigger_price, stop_price,
          status, status_reason,
          created_at, updated_at, submitted_at,
          simulation_rule, meta
        ) VALUES (
          :order_id, :idea_id, :symbol, :market, :provider, :interval,
          :side, 'limit', NULL,
          1.0, NULL,
          :limit_price, NULL, :stop_price,
          'filled', 'entry_simulated',
          CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz), CAST(:submitted_at AS timestamptz),
          CAST(:simulation_rule AS jsonb), CAST(:meta AS jsonb)
        )
        ON CONFLICT (order_id) DO NOTHING
        """
    )
    ins_fill = text(
        """
        INSERT INTO paper_fills (
          fill_id, order_id, idea_id, symbol, side,
          fill_qty, fill_price, fill_notional, fee, fee_currency, slippage_bps,
          fill_time, fill_seq, fill_source, meta
        ) VALUES (
          :fill_id, :order_id, :idea_id, :symbol, :side,
          1.0, :fill_price, NULL, NULL, NULL, NULL,
          CAST(:fill_time AS timestamptz), 1, 'paper_bar_touch', CAST(:meta AS jsonb)
        )
        ON CONFLICT (idea_id, fill_seq) DO NOTHING
        """
    )
    params_order = {
        "order_id": order_id,
        "idea_id": idea_id,
        "symbol": symbol[:32],
        "market": market[:16],
        "provider": provider[:32],
        "interval": interval[:16],
        "side": side[:16],
        "limit_price": float(limit_px) if limit_px is not None else None,
        "stop_price": float(stop_px) if isinstance(stop_px, (int, float)) else None,
        "created_at": fill_time.isoformat(),
        "updated_at": now.isoformat(),
        "submitted_at": fill_time.isoformat(),
        "simulation_rule": _jb(simulation_rule),
        "meta": _jb({"phase": 1}),
    }
    params_fill = {
        "fill_id": fill_id,
        "order_id": order_id,
        "idea_id": idea_id,
        "symbol": symbol[:32],
        "side": side[:16],
        "fill_price": float(fill_px),
        "fill_time": fill_time.isoformat(),
        "meta": _jb({"role": "entry"}),
    }
    try:
        with eng.begin() as conn:
            conn.execute(ins_order, params_order)
            conn.execute(ins_fill, params_fill)
    except Exception as exc:
        logger.error(
            "[PaperTrade] create_entry_order_and_fill_failed idea_id={} error_type={} message={}\n{}",
            idea_id,
            type(exc).__name__,
            str(exc),
            traceback.format_exc(),
        )


def create_exit_fill(idea: dict[str, Any], *, close_reason: str, now_utc: datetime | None = None) -> None:
    """filled -> closed(tp/sl)：在同一 order 下插入 exit fill（fill_seq=2）。幂等。"""
    eng = _engine()
    if eng is None:
        return
    now = now_utc or datetime.now(timezone.utc)
    idea_id = str(idea.get("idea_id") or "").strip()
    if not idea_id:
        return
    if has_exit_fill_for_idea(idea_id):
        return
    if not has_entry_fill_for_idea(idea_id):
        logger.warning("[PaperTrade] skip exit fill: no entry fill idea_id={}", idea_id)
        return

    order_id = stable_order_id(idea_id)
    fill_id = stable_fill_id(idea_id, 2)
    symbol = str(idea.get("symbol") or "")
    direction = str(idea.get("direction") or "long")
    side = _side_from_direction(direction)
    fill_px = idea.get("closed_price")
    if fill_px is None:
        logger.warning("[PaperTrade] skip exit fill: no closed_price idea_id={}", idea_id)
        return
    fill_time = _parse_fill_time(str(idea.get("closed_at_utc") or ""), fallback=now)
    meta = {"role": "exit", "close_reason": close_reason}

    ins_fill = text(
        """
        INSERT INTO paper_fills (
          fill_id, order_id, idea_id, symbol, side,
          fill_qty, fill_price, fill_notional, fee, fee_currency, slippage_bps,
          fill_time, fill_seq, fill_source, meta
        ) VALUES (
          :fill_id, :order_id, :idea_id, :symbol, :side,
          1.0, :fill_price, NULL, NULL, NULL, NULL,
          CAST(:fill_time AS timestamptz), 2, 'paper_bar_touch', CAST(:meta AS jsonb)
        )
        ON CONFLICT (idea_id, fill_seq) DO NOTHING
        """
    )
    params_fill = {
        "fill_id": fill_id,
        "order_id": order_id,
        "idea_id": idea_id,
        "symbol": symbol[:32],
        "side": side[:16],
        "fill_price": float(fill_px),
        "fill_time": fill_time.isoformat(),
        "meta": _jb(meta),
    }
    try:
        with eng.begin() as conn:
            conn.execute(ins_fill, params_fill)
    except Exception as exc:
        logger.error(
            "[PaperTrade] create_exit_fill_failed idea_id={} close_reason={} error_type={} message={}\n{}",
            idea_id,
            close_reason,
            type(exc).__name__,
            str(exc),
            traceback.format_exc(),
        )


def fetch_paper_trade_monitor() -> dict[str, Any] | None:
    """PG 全局对账计数；非 postgres/dualwrite 或无 engine 时返回 None。"""
    eng = _engine()
    if eng is None:
        return None
    try:
        with eng.connect() as conn:
            n_orders = conn.execute(text("SELECT COUNT(*) FROM paper_orders")).scalar_one()
            n_fills = conn.execute(text("SELECT COUNT(*) FROM paper_fills")).scalar_one()
            n_bad_entry = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM journal_ideas ji
                    WHERE ji.status = 'filled'
                    AND NOT EXISTS (
                      SELECT 1 FROM paper_fills pf
                      WHERE pf.idea_id = ji.idea_id AND pf.fill_seq = 1
                    )
                    """
                )
            ).scalar_one()
            n_bad_exit = conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM journal_ideas ji
                    WHERE ji.status = 'closed' AND ji.exit_status IN ('tp', 'sl')
                    AND NOT EXISTS (
                      SELECT 1 FROM paper_fills pf
                      WHERE pf.idea_id = ji.idea_id AND pf.fill_seq = 2
                    )
                    """
                )
            ).scalar_one()
        return {
            "paper_order_count": int(n_orders),
            "paper_fill_count": int(n_fills),
            "filled_idea_without_entry_fill_count": int(n_bad_entry),
            "closed_idea_without_exit_fill_count": int(n_bad_exit),
        }
    except Exception as exc:
        logger.warning(
            "[PaperTrade] fetch_paper_trade_monitor_failed error_type={} message={}",
            type(exc).__name__,
            str(exc),
        )
        return None
