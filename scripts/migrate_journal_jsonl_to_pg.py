#!/usr/bin/env python3
"""
将历史 trade_journal.jsonl 幂等迁入 PostgreSQL（不修改源 JSONL）。

事件补齐策略（与仓库迁移计划「补强 3」一致）：
- 基线：每条 idea 至少一条 idea_created（payload 含 source=migration_infer）。
- 增强（默认开启 --infer-events）：在基线之上按终态字段推导最小事件集：
  - filled_at_utc 有值且非 expired/closed 路径时补 filled；
  - status=expired 补 expired；
  - status=closed 且 exit_status=tp/sl 分别补 closed_tp / closed_sl；
  - 无法归类时补一条带 source=migration_infer 的 status_changed，payload 写明原因。
若 journal_events 中该 idea_id 已有任意行，则跳过该 idea 的事件写入（保留线上双写产生的事件）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from sqlalchemy import create_engine, text  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            o = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(o, dict):
            out.append(o)
    return out


def _idea_to_params(idea: dict[str, Any]) -> dict[str, Any]:
    zone = idea.get("entry_zone")
    zl = zh = None
    if isinstance(zone, list) and len(zone) == 2:
        zl = float(min(float(zone[0]), float(zone[1])))
        zh = float(max(float(zone[0]), float(zone[1])))
    tps = idea.get("take_profit_levels") or []
    tp1 = float(tps[0]) if isinstance(tps, list) and tps and isinstance(tps[0], (int, float)) else None
    tp2 = float(tps[1]) if isinstance(tps, list) and len(tps) > 1 and isinstance(tps[1], (int, float)) else None

    def jb(v: Any) -> str:
        if v is None:
            return "{}"
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return json.dumps(v, ensure_ascii=False)

    return {
        "idea_id": str(idea.get("idea_id") or ""),
        "symbol": str(idea.get("symbol") or ""),
        "asset_name": idea.get("name"),
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
        "entry_zone_low": zl,
        "entry_zone_high": zh,
        "signal_last": idea.get("signal_last"),
        "stop_loss": idea.get("stop_loss"),
        "tp1": tp1,
        "tp2": tp2,
        "rr": idea.get("rr"),
        "wyckoff_bias": idea.get("wyckoff_bias"),
        "mtf_aligned": idea.get("mtf_aligned"),
        "structure_flags": jb(idea.get("structure_flags")),
        "tags": jb(idea.get("tags")),
        "strategy_reason": idea.get("strategy_reason"),
        "lifecycle_v1": jb(idea.get("lifecycle_v1")),
        "meta": jb(idea.get("meta")),
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


def _infer_events(idea: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = [("idea_created", {"source": "migration_infer"})]
    status = str(idea.get("status") or "")
    ex = str(idea.get("exit_status") or "")
    if idea.get("filled_at_utc") and status not in {"expired"}:
        out.append(("filled", {"source": "migration_infer"}))
    if status == "expired":
        out.append(("expired", {"source": "migration_infer"}))
    elif status == "closed":
        if ex == "tp":
            out.append(("closed_tp", {"source": "migration_infer"}))
        elif ex == "sl":
            out.append(("closed_sl", {"source": "migration_infer"}))
        else:
            out.append(
                (
                    "status_changed",
                    {"source": "migration_infer", "note": "closed_unknown_exit", "exit_status": ex},
                )
            )
    return out


UPSERT_SQL = text(
    """
    INSERT INTO journal_ideas (
      idea_id, symbol, asset_name, market, provider, interval,
      plan_type, direction, status, exit_status,
      entry_type, order_kind_cn,
      entry_price, entry_zone_low, entry_zone_high, signal_last, stop_loss,
      tp1, tp2, rr,
      wyckoff_bias, mtf_aligned, structure_flags, tags,
      strategy_reason, lifecycle_v1, meta,
      created_at, updated_at, valid_until, filled_at, closed_at,
      fill_price, closed_price, realized_pnl_pct, unrealized_pnl_pct
    ) VALUES (
      :idea_id, :symbol, :asset_name, :market, :provider, :interval,
      :plan_type, :direction, :status, :exit_status,
      :entry_type, :order_kind_cn,
      :entry_price, :entry_zone_low, :entry_zone_high, :signal_last, :stop_loss,
      :tp1, :tp2, :rr,
      :wyckoff_bias, :mtf_aligned, CAST(:structure_flags AS jsonb), CAST(:tags AS jsonb),
      :strategy_reason, CAST(:lifecycle_v1 AS jsonb), CAST(:meta AS jsonb),
      CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz),
      CAST(:valid_until AS timestamptz), CAST(:filled_at AS timestamptz), CAST(:closed_at AS timestamptz),
      :fill_price, :closed_price, :realized_pnl_pct, :unrealized_pnl_pct
    )
    ON CONFLICT (idea_id) DO UPDATE SET
      symbol = EXCLUDED.symbol,
      asset_name = EXCLUDED.asset_name,
      market = EXCLUDED.market,
      provider = EXCLUDED.provider,
      interval = EXCLUDED.interval,
      plan_type = EXCLUDED.plan_type,
      direction = EXCLUDED.direction,
      status = EXCLUDED.status,
      exit_status = EXCLUDED.exit_status,
      entry_type = EXCLUDED.entry_type,
      order_kind_cn = EXCLUDED.order_kind_cn,
      entry_price = EXCLUDED.entry_price,
      entry_zone_low = EXCLUDED.entry_zone_low,
      entry_zone_high = EXCLUDED.entry_zone_high,
      signal_last = EXCLUDED.signal_last,
      stop_loss = EXCLUDED.stop_loss,
      tp1 = EXCLUDED.tp1,
      tp2 = EXCLUDED.tp2,
      rr = EXCLUDED.rr,
      wyckoff_bias = EXCLUDED.wyckoff_bias,
      mtf_aligned = EXCLUDED.mtf_aligned,
      structure_flags = EXCLUDED.structure_flags,
      tags = EXCLUDED.tags,
      strategy_reason = EXCLUDED.strategy_reason,
      lifecycle_v1 = EXCLUDED.lifecycle_v1,
      meta = EXCLUDED.meta,
      created_at = EXCLUDED.created_at,
      updated_at = EXCLUDED.updated_at,
      valid_until = EXCLUDED.valid_until,
      filled_at = EXCLUDED.filled_at,
      closed_at = EXCLUDED.closed_at,
      fill_price = EXCLUDED.fill_price,
      closed_price = EXCLUDED.closed_price,
      realized_pnl_pct = EXCLUDED.realized_pnl_pct,
      unrealized_pnl_pct = EXCLUDED.unrealized_pnl_pct
    """
)


def main() -> int:
    p = argparse.ArgumentParser(description="Migrate trade_journal.jsonl to PostgreSQL")
    p.add_argument("--jsonl", type=Path, default=_REPO / "output" / "trade_journal.jsonl")
    p.add_argument("--dsn", default="", help="覆盖 config 内 postgres.dsn")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--infer-events", action="store_true", default=True)
    p.add_argument("--no-infer-events", action="store_true", help="仅 idea_created 基线")
    args = p.parse_args()

    from config.runtime_config import get_postgres_dsn

    dsn = (args.dsn or "").strip() or get_postgres_dsn()
    if not dsn:
        print("缺少 postgres dsn（config 或 --dsn）", file=sys.stderr)
        return 2

    infer = args.infer_events and not args.no_infer_events
    ideas = _load_jsonl(args.jsonl.resolve())
    if not ideas:
        print("无 JSONL 记录，退出")
        return 0

    engine = create_engine(dsn, future=True)
    ok = skip_ev = fail = 0
    with engine.begin() as conn:
        for idea in ideas:
            iid = str(idea.get("idea_id") or "")
            if not iid:
                fail += 1
                continue
            try:
                if args.dry_run:
                    ok += 1
                    continue
                conn.execute(UPSERT_SQL, _idea_to_params(idea))
                n_ev = conn.execute(
                    text("SELECT COUNT(*) FROM journal_events WHERE idea_id = :i"),
                    {"i": iid},
                ).scalar_one()
                if int(n_ev) > 0:
                    skip_ev += 1
                else:
                    evs = _infer_events(idea) if infer else [("idea_created", {"source": "migration_infer"})]
                    for et, payload in evs:
                        conn.execute(
                            text(
                                """
                                INSERT INTO journal_events (idea_id, event_type, old_status, new_status, event_time, payload)
                                VALUES (:idea_id, :event_type, NULL, NULL, CAST(:event_time AS timestamptz), CAST(:payload AS jsonb))
                                """
                            ),
                            {
                                "idea_id": iid,
                                "event_type": et,
                                "event_time": _utcnow_iso(),
                                "payload": json.dumps(payload, ensure_ascii=False),
                            },
                        )
                ok += 1
            except Exception as e:
                print(f"FAIL {iid}: {e}", file=sys.stderr)
                fail += 1

    print(f"ideas_total={len(ideas)} upsert_ok={ok} skip_existing_events={skip_ev} fail={fail} dry_run={args.dry_run}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
