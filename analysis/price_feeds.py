from __future__ import annotations

from typing import Any

from .gold_api import fetch_ohlcv_goldapi
from tools.gateio.client import fetch_ohlcv_gateio as fetch_ohlcv_gateio_client
from tools.tickflow.client import fetch_ohlcv_tickflow as fetch_ohlcv_tickflow_client


def _to_tickflow_symbol(ticker: str, market: str) -> str:
    raw = ticker.strip().upper()
    mkt = market.strip().upper()
    if "." in raw:
        return raw
    if mkt == "CN":
        if raw.startswith(("6", "9")):
            return f"{raw}.SH"
        if raw.startswith(("0", "3")):
            return f"{raw}.SZ"
    if mkt == "US":
        return f"{raw}.US"
    if mkt == "HK":
        return f"{raw}.HK"
    return raw


def fetch_ohlcv_tickflow(*, ticker: str, market: str, interval: str, limit: int) -> list[dict[str, Any]]:
    symbol = _to_tickflow_symbol(ticker=ticker, market=market)
    return fetch_ohlcv_tickflow_client(symbol=symbol, interval=interval, limit=limit)


def _to_gateio_pair(ticker: str, market: str) -> str:
    raw = ticker.strip().upper().replace("-", "_")
    mkt = (market or "").strip().upper()
    if "_" in raw:
        return raw
    if mkt in {"CRYPTO", "COIN", "DIGITAL_ASSET"} and raw.endswith("USDT"):
        return f"{raw[:-4]}_USDT"
    return raw


def fetch_ohlcv_gateio(*, ticker: str, market: str, interval: str, limit: int) -> list[dict[str, Any]]:
    pair = _to_gateio_pair(ticker=ticker, market=market)
    return fetch_ohlcv_gateio_client(pair=pair, interval=interval, limit=limit)


def fetch_ohlcv(provider: str, ticker: str, market: str, interval: str, limit: int) -> list[dict[str, Any]]:
    p = (provider or "tickflow").strip().lower()
    if p == "tickflow":
        return fetch_ohlcv_tickflow(ticker=ticker, market=market, interval=interval, limit=limit)
    if p == "gateio":
        return fetch_ohlcv_gateio(ticker=ticker, market=market, interval=interval, limit=limit)
    if p in {"goldapi", "gold-api", "gold_api"}:
        return fetch_ohlcv_goldapi(ticker=ticker, market=market, interval=interval, limit=limit)
    raise ValueError(f"暂不支持的 provider: {provider}（支持 tickflow/gateio/goldapi）")
