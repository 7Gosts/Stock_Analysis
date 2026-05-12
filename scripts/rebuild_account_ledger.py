#!/usr/bin/env python3
"""
Data repair tool: rebuild account_ledger from paper_fills + journal_ideas.

Usage:
  python scripts/rebuild_account_ledger.py [--currency CNY|USD] [--dry-run]

This script:
1. Queries paper_fills and journal_ideas for completed trades
2. Reconstructs account_ledger snapshots (init -> open -> close)
3. Writes to DB or prints (--dry-run)

WARNING: This is a destructive operation. Back up your database first.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add repo root to path
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from loguru import logger
from sqlalchemy import text

from analysis.position_sizing import map_market_to_currency
from app.db import get_sqlalchemy_engine
from config.runtime_config import get_database_backend, get_account_initial_balance


def rebuild_ledger_for_currency(currency: str, *, dry_run: bool = False) -> None:
    backend = get_database_backend()
    if backend not in {"postgres", "dualwrite"}:
        logger.warning("[Rebuild] No DB backend; skipping")
        return

    engine = get_sqlalchemy_engine()
    if engine is None:
        logger.error("[Rebuild] No engine")
        return

    initial_balance = get_account_initial_balance(currency)
    if initial_balance <= 0:
        logger.warning("[Rebuild] No initial balance for {}", currency)
        return

    reconstructed: list[dict[str, Any]] = []
    balance = initial_balance
    used_margin = 0.0

    # Initialize
    reconstructed.append(
        {
            "account_id": currency,
            "balance": balance,
            "available": balance - used_margin,
            "used_margin": used_margin,
            "unrealized_pnl": 0.0,
            "equity": balance,
            "reason": "init",
            "snapshot_time": datetime.now(timezone.utc),
        }
    )

    try:
        with engine.connect() as conn:
            # Fetch all open positions for this currency's markets (infer from open positions)
            positions = conn.execute(
                text(
                    """
                    SELECT ap.id, ap.symbol, ap.qty, ap.entry_price, ap.entry_notional,
                           ap.exit_price, ap.realized_pnl, ap.opened_at, ap.closed_at
                    FROM account_positions ap
                    WHERE ap.account_id = :aid
                    ORDER BY COALESCE(ap.opened_at, ap.closed_at)
                    """
                ),
                {"aid": currency},
            )

            for row in positions:
                pos_id, symbol, qty, entry_price, entry_notional, exit_price, realized_pnl, opened_at, closed_at = (
                    row[0],
                    row[1],
                    float(row[2] or 0.0),
                    float(row[3] or 0.0),
                    float(row[4] or 0.0),
                    row[5],
                    row[6],
                    row[7],
                    row[8],
                )

                # Snapshot on open
                used_margin += entry_notional
                reconstructed.append(
                    {
                        "account_id": currency,
                        "balance": balance,
                        "available": balance - used_margin,
                        "used_margin": used_margin,
                        "unrealized_pnl": 0.0,
                        "equity": balance,
                        "reason": "position_open",
                        "snapshot_time": opened_at or datetime.now(timezone.utc),
                    }
                )

                # Snapshot on close (if closed)
                if closed_at and exit_price and realized_pnl is not None:
                    balance += float(realized_pnl)
                    used_margin -= entry_notional
                    reconstructed.append(
                        {
                            "account_id": currency,
                            "balance": balance,
                            "available": balance - used_margin,
                            "used_margin": used_margin,
                            "unrealized_pnl": 0.0,
                            "equity": balance,
                            "reason": "position_close",
                            "snapshot_time": closed_at,
                        }
                    )

        logger.info("[Rebuild] {} snapshots reconstructed for {}", len(reconstructed), currency)

        if dry_run:
            for snap in reconstructed:
                logger.info("[DRY-RUN] {} balance={} available={} used_margin={}", snap["reason"], snap["balance"], snap["available"], snap["used_margin"])
            return

        # Write to DB
        with engine.begin() as conn:
            # Delete existing ledger for this account
            conn.execute(text("DELETE FROM account_ledger WHERE account_id = :aid"), {"aid": currency})
            # Insert reconstructed
            for snap in reconstructed:
                conn.execute(
                    text(
                        "INSERT INTO account_ledger (account_id, balance, available, used_margin, unrealized_pnl, equity, snapshot_time, reason, meta) VALUES (:aid, :bal, :avail, :used, :u, :equity, CAST(:t AS timestamptz), :reason, '{}'::jsonb)"
                    ),
                    {
                        "aid": snap["account_id"],
                        "bal": snap["balance"],
                        "avail": snap["available"],
                        "used": snap["used_margin"],
                        "u": snap["unrealized_pnl"],
                        "equity": snap["equity"],
                        "t": snap["snapshot_time"].isoformat(),
                        "reason": snap["reason"],
                    },
                )
        logger.info("[Rebuild] Ledger rebuilt for {} (committed to DB)", currency)

    except Exception as e:
        logger.error("[Rebuild] Failed: {}", str(e))


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild account_ledger from positions")
    parser.add_argument("--currency", default="USD", help="Currency to rebuild (default: USD)")
    parser.add_argument("--dry-run", action="store_true", help="Print instead of writing")
    args = parser.parse_args()

    logger.info("[Rebuild] Starting rebuild for currency={} dry_run={}", args.currency, args.dry_run)
    rebuild_ledger_for_currency(args.currency.upper(), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
