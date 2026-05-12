from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from persistence.db import get_sqlalchemy_engine
from persistence.sql_loader import load_sql_text
from analysis.trade_journal import has_active_idea as _has_active_idea


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _dump_json(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    return json.dumps(val, ensure_ascii=False)


_IDEA_INSERT_SQL = load_sql_text("journal/idea_insert.sql")
_IDEA_UPSERT_SQL = load_sql_text("journal/idea_upsert.sql")
class PostgresJournalRepository:
    """PostgreSQL 台账；行字典与 ledger_stats / 编排层字段对齐。"""

    def __init__(self) -> None:
        eng = get_sqlalchemy_engine()
        if eng is None:
            raise RuntimeError("PostgresJournalRepository 需要有效 database.postgres.dsn（见 config/analysis_defaults.yaml）")
        self._engine: Engine = eng

    def list_entries(self) -> list[dict[str, Any]]:
        sql = text("SELECT * FROM journal_ideas ORDER BY id ASC")
        out: list[dict[str, Any]] = []
        with self._engine.connect() as conn:
            for row in conn.execute(sql).mappings():
                out.append(self._db_row_to_idea(dict(row)))
        return out

    def save_entries(self, entries: list[dict[str, Any]]) -> None:
        with self._engine.begin() as conn:
            incoming_ids = {
                str(idea.get("idea_id") or "")
                for idea in entries
                if str(idea.get("idea_id") or "")
            }
            existing_ids = {
                str(row[0])
                for row in conn.execute(text("SELECT idea_id FROM journal_ideas"))
                if row and row[0]
            }
            for stale_id in sorted(existing_ids - incoming_ids):
                conn.execute(text("DELETE FROM journal_ideas WHERE idea_id = :idea_id"), {"idea_id": stale_id})
            for idea in entries:
                self._upsert_idea(conn, idea)

    def append_idea(self, idea: dict[str, Any]) -> None:
        params = self._idea_to_row(idea)
        with self._engine.begin() as conn:
            self._insert_idea(conn, idea)
            conn.execute(
                text(
                    """
                    INSERT INTO journal_events (idea_id, event_type, old_status, new_status, event_time, payload)
                    VALUES (:idea_id, 'idea_created', NULL, NULL, CAST(:event_time AS timestamptz), CAST(:payload AS jsonb))
                    """
                ),
                {
                    "idea_id": params["idea_id"],
                    "event_time": params["created_at"] or _utcnow_iso(),
                    "payload": json.dumps({"source": "append_idea"}, ensure_ascii=False),
                },
            )

    def update_idea(self, idea_id: str, patch: dict[str, Any]) -> None:
        with self._engine.begin() as conn:
            row = conn.execute(
                text("SELECT * FROM journal_ideas WHERE idea_id = :idea_id LIMIT 1"),
                {"idea_id": idea_id},
            ).mappings().first()
            if row is None:
                logger.warning("[JournalPG] update_idea missing idea_id={}", idea_id)
                return
            current = self._db_row_to_idea(dict(row))
            for key, value in patch.items():
                current[key] = value
            params = self._idea_to_row(current)
            sets = [
                "status = :status",
                "exit_status = :exit_status",
                "updated_at = CAST(:updated_at AS timestamptz)",
                "valid_until = CAST(:valid_until AS timestamptz)",
                "filled_at = CAST(:filled_at AS timestamptz)",
                "closed_at = CAST(:closed_at AS timestamptz)",
                "fill_price = :fill_price",
                "closed_price = :closed_price",
                "realized_pnl_pct = :realized_pnl_pct",
                "unrealized_pnl_pct = :unrealized_pnl_pct",
                "lifecycle_v1 = CAST(:lifecycle_v1 AS jsonb)",
                "meta = CAST(:meta AS jsonb)",
            ]
            conn.execute(text(f"UPDATE journal_ideas SET {', '.join(sets)} WHERE idea_id = :idea_id"), params)

    def append_event(self, idea_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO journal_events (idea_id, event_type, old_status, new_status, event_time, payload)
                    VALUES (:idea_id, :event_type, NULL, NULL, CAST(:event_time AS timestamptz), CAST(:payload AS jsonb))
                    """
                ),
                {
                    "idea_id": idea_id,
                    "event_type": event_type,
                    "event_time": _utcnow_iso(),
                    "payload": json.dumps(payload or {}, ensure_ascii=False),
                },
            )

    def has_active_idea(
        self,
        *,
        symbol: str,
        interval: str,
        direction: str,
        plan_type: str,
    ) -> bool:
        return _has_active_idea(
            self.list_entries(),
            symbol=symbol,
            interval=interval,
            direction=direction,
            plan_type=plan_type,
        )

    def _insert_idea(self, conn: Connection, idea: dict[str, Any]) -> None:
        conn.execute(_IDEA_INSERT_SQL, self._idea_to_row(idea))

    def _upsert_idea(self, conn: Connection, idea: dict[str, Any]) -> None:
        conn.execute(_IDEA_UPSERT_SQL, self._idea_to_row(idea))

    def _idea_to_row(self, idea: dict[str, Any]) -> dict[str, Any]:
        zone = idea.get("entry_zone")
        zone_low = zone_high = None
        if isinstance(zone, list) and len(zone) == 2:
            zone_low = float(min(float(zone[0]), float(zone[1])))
            zone_high = float(max(float(zone[0]), float(zone[1])))
        tps = idea.get("take_profit_levels") or []
        tp1 = float(tps[0]) if isinstance(tps, list) and tps and isinstance(tps[0], (int, float)) else None
        tp2 = float(tps[1]) if isinstance(tps, list) and len(tps) > 1 and isinstance(tps[1], (int, float)) else None
        return {
            "idea_id": str(idea.get("idea_id") or ""),
            "symbol": str(idea.get("symbol") or ""),
            "asset_name": idea.get("name") or idea.get("asset"),
            "market": str(idea.get("market") or "UNK"),
            "provider": str(idea.get("provider") or "tickflow"),
            "interval": str(idea.get("interval") or "1d"),
            "plan_type": str(idea.get("plan_type") or "tactical"),
            "direction": str(idea.get("direction") or "long"),
            "status": str(idea.get("status") or "pending"),
            "exit_status": idea.get("exit_status"),
            "entry_type": idea.get("entry_type"),
            "order_kind_cn": idea.get("order_kind_cn"),
            "entry_price": idea.get("entry_price"),
            "entry_zone_low": zone_low,
            "entry_zone_high": zone_high,
            "signal_last": idea.get("signal_last"),
            "stop_loss": idea.get("stop_loss"),
            "tp1": tp1,
            "tp2": tp2,
            "rr": idea.get("rr"),
            "wyckoff_bias": idea.get("wyckoff_bias"),
            "mtf_aligned": idea.get("mtf_aligned"),
            "structure_flags": _dump_json(idea.get("structure_flags")) or "{}",
            "tags": _dump_json(idea.get("tags")) or "{}",
            "strategy_reason": idea.get("strategy_reason"),
            "lifecycle_v1": _dump_json(idea.get("lifecycle_v1")) or "{}",
            "meta": _dump_json(idea.get("meta")) or "{}",
            "created_at": idea.get("created_at_utc") or _utcnow_iso(),
            "updated_at": idea.get("updated_at_utc") or idea.get("created_at_utc") or _utcnow_iso(),
            "valid_until": idea.get("valid_until_utc"),
            "filled_at": idea.get("filled_at_utc"),
            "closed_at": idea.get("closed_at_utc"),
            "fill_price": idea.get("fill_price"),
            "closed_price": idea.get("closed_price"),
            "realized_pnl_pct": idea.get("realized_pnl_pct"),
            "unrealized_pnl_pct": idea.get("unrealized_pnl_pct"),
        }

    def _db_row_to_idea(self, row: dict[str, Any]) -> dict[str, Any]:
        def iso(value: Any) -> str | None:
            if value is None:
                return None
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                return value.astimezone(timezone.utc).isoformat()
            return str(value)

        zone_low, zone_high = row.get("entry_zone_low"), row.get("entry_zone_high")
        entry_zone = None
        if zone_low is not None and zone_high is not None:
            entry_zone = [float(zone_low), float(zone_high)]
        take_profit_levels: list[float] = []
        if row.get("tp1") is not None:
            take_profit_levels.append(float(row["tp1"]))
        if row.get("tp2") is not None:
            take_profit_levels.append(float(row["tp2"]))

        idea: dict[str, Any] = {
            "idea_id": row.get("idea_id"),
            "symbol": row.get("symbol"),
            "name": row.get("asset_name"),
            "market": row.get("market"),
            "provider": row.get("provider"),
            "interval": row.get("interval"),
            "plan_type": row.get("plan_type"),
            "direction": row.get("direction"),
            "status": row.get("status"),
            "exit_status": row.get("exit_status"),
            "entry_type": row.get("entry_type"),
            "order_kind_cn": row.get("order_kind_cn"),
            "entry_price": float(row["entry_price"]) if row.get("entry_price") is not None else None,
            "entry_zone": entry_zone,
            "signal_last": float(row["signal_last"]) if row.get("signal_last") is not None else None,
            "stop_loss": float(row["stop_loss"]) if row.get("stop_loss") is not None else None,
            "take_profit_levels": take_profit_levels if take_profit_levels else None,
            "rr": float(row["rr"]) if row.get("rr") is not None else None,
            "wyckoff_bias": row.get("wyckoff_bias"),
            "mtf_aligned": row.get("mtf_aligned"),
            "structure_flags": row.get("structure_flags"),
            "tags": row.get("tags"),
            "strategy_reason": row.get("strategy_reason"),
            "lifecycle_v1": row.get("lifecycle_v1"),
            "meta": row.get("meta"),
            "created_at_utc": iso(row.get("created_at")),
            "updated_at_utc": iso(row.get("updated_at")),
            "valid_until_utc": iso(row.get("valid_until")),
            "filled_at_utc": iso(row.get("filled_at")),
            "closed_at_utc": iso(row.get("closed_at")),
            "fill_price": float(row["fill_price"]) if row.get("fill_price") is not None else None,
            "closed_price": float(row["closed_price"]) if row.get("closed_price") is not None else None,
            "realized_pnl_pct": float(row["realized_pnl_pct"]) if row.get("realized_pnl_pct") is not None else None,
            "unrealized_pnl_pct": float(row["unrealized_pnl_pct"]) if row.get("unrealized_pnl_pct") is not None else None,
        }
        required = {"status", "symbol", "interval", "idea_id", "direction", "plan_type", "market", "provider"}
        return {key: value for key, value in idea.items() if value is not None or key in required}