#!/usr/bin/env python3
"""
Scheduled task: update unrealized PnL for open positions using latest market prices.

Usage:
  python scripts/account_mark_to_market.py

This script:
1. Fetches latest price for each open position symbol
2. Updates account_positions table with unrealized_pnl
3. Writes account_ledger snapshot with updated equity
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Add repo root to path
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from loguru import logger
from sqlalchemy import text

from app.account_service import mark_to_market
from app.db import get_sqlalchemy_engine
from config.runtime_config import get_database_backend
from analysis.price_feeds import fetch_ohlcv


def _get_latest_prices_for_positions() -> dict[str, float]:
    """Fetch latest price for each open position symbol."""
    backend = get_database_backend()
    if backend not in {"postgres", "dualwrite"}:
        logger.warning("[MTM] No DB backend configured; skipping mark_to_market")
        return {}

    engine = get_sqlalchemy_engine()
    if engine is None:
        return {}

    symbol_price_map = {}
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT DISTINCT symbol FROM account_positions WHERE status = 'open'")
            )
            symbols = [r[0] for r in rows if r[0]]
            logger.info("[MTM] Fetching prices for {} symbols", len(symbols))

            for symbol in symbols:
                try:
                    # Fetch 1d OHLCV; take close price from latest bar
                    ohlcv = fetch_ohlcv(symbol, provider="tickflow", interval="1d", limit=1)
                    if ohlcv and len(ohlcv) > 0:
                        latest_bar = ohlcv[-1]
                        symbol_price_map[symbol] = float(latest_bar.get("close", 0.0))
                        logger.debug("[MTM] {} price: {}", symbol, symbol_price_map[symbol])
                except Exception as e:
                    logger.warning("[MTM] Failed to fetch price for {}: {}", symbol, str(e))
    except Exception as e:
        logger.error("[MTM] Failed to query open positions: {}", str(e))
        return {}

    return symbol_price_map


def main() -> None:
    logger.info("[MTM] Starting mark_to_market update")
    prices = _get_latest_prices_for_positions()
    if not prices:
        logger.warning("[MTM] No prices fetched; skipping MTM update")
        return
    mark_to_market(prices)
    logger.info("[MTM] Mark_to_market completed")


if __name__ == "__main__":
    main()
