from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .gold_api import fetch_ohlcv_goldapi


def _lazy_ak_import():
    import akshare as ak

    return ak


def _normalize_cn_symbol(symbol: str) -> str:
    raw = symbol.strip().upper()
    if "." in raw:
        raw = raw.split(".")[0]
    if raw.startswith(("SH", "SZ")):
        raw = raw[2:]
    return raw


def _interval_to_ak_period(interval: str) -> str:
    mapping = {"1d": "daily", "1wk": "weekly", "1mo": "monthly"}
    if interval not in mapping:
        raise ValueError(f"akshare 暂仅支持 1d/1wk/1mo，当前: {interval}")
    return mapping[interval]


def _rows_from_ak_df(df: Any) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    rows: list[dict[str, Any]] = []
    cols = set(str(c) for c in df.columns)

    if {"日期", "开盘", "收盘", "最高", "最低"}.issubset(cols):
        for _, row in df.iterrows():
            ts = datetime.strptime(str(row["日期"]), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            rows.append(
                {
                    "time": ts.isoformat(),
                    "open": float(row["开盘"]),
                    "high": float(row["最高"]),
                    "low": float(row["最低"]),
                    "close": float(row["收盘"]),
                    "volume": float(row.get("成交量", 0.0)),
                }
            )
        return rows

    low_cols = {str(c).lower(): c for c in df.columns}
    if {"open", "high", "low", "close"}.issubset(low_cols):
        date_col = low_cols.get("date")
        open_col = low_cols["open"]
        high_col = low_cols["high"]
        low_col = low_cols["low"]
        close_col = low_cols["close"]
        vol_col = low_cols.get("volume")
        for idx, row in df.iterrows():
            dt_val = row[date_col] if date_col else idx
            if hasattr(dt_val, "to_pydatetime"):
                ts = dt_val.to_pydatetime()
            elif hasattr(dt_val, "strftime"):
                ts = datetime.strptime(dt_val.strftime("%Y-%m-%d"), "%Y-%m-%d")
            else:
                ts = datetime.strptime(str(dt_val)[:10], "%Y-%m-%d")
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
            rows.append(
                {
                    "time": ts.isoformat(),
                    "open": float(row[open_col]),
                    "high": float(row[high_col]),
                    "low": float(row[low_col]),
                    "close": float(row[close_col]),
                    "volume": float(row[vol_col]) if vol_col else 0.0,
                }
            )
        return rows

    return []


def fetch_ohlcv_akshare(*, ticker: str, market: str, interval: str, limit: int) -> list[dict[str, Any]]:
    ak = _lazy_ak_import()
    lim = max(30, min(int(limit), 1000))
    mkt = (market or "").strip().upper()

    if mkt == "CN":
        period = _interval_to_ak_period(interval)
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=1100)
        df = ak.stock_zh_a_hist(
            symbol=_normalize_cn_symbol(ticker),
            period=period,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="",
        )
        rows = _rows_from_ak_df(df)
    elif mkt == "US":
        if interval != "1d":
            raise ValueError("akshare 美股接口当前仅支持 1d 日线")
        df = ak.stock_us_daily(symbol=ticker.strip().upper(), adjust="")
        rows = _rows_from_ak_df(df)
    else:
        raise ValueError(f"未知 market: {market}（支持 CN/US）")

    rows = [r for r in rows if r["open"] > 0 and r["high"] > 0 and r["low"] > 0 and r["close"] > 0]
    rows.sort(key=lambda x: x["time"])
    return rows[-lim:]


def _http_get_json(url: str, headers: dict[str, str] | None = None, timeout: float = 30.0) -> Any:
    h = {
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    if headers:
        h.update(headers)
    req = Request(url, headers=h)
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


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
    # TickFlow 免费服务可直接用，但仅支持日级别；有 API Key 时自动走完整服务。
    api_key = os.getenv("TICKFLOW_API_KEY", "").strip()
    base = "https://api.tickflow.org" if api_key else "https://free-api.tickflow.org"
    period = interval if interval in {"1d", "1w", "1M", "1Q", "1Y"} else "1d"
    query = urlencode(
        {
            "symbol": _to_tickflow_symbol(ticker=ticker, market=market),
            "period": period,
            "count": str(max(30, min(int(limit), 10000))),
            "adjust": "none",
        }
    )
    url = f"{base}/v1/klines?{query}"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    payload = _http_get_json(url, headers=headers)
    data = payload.get("data") or {}
    ts = data.get("timestamp") or []
    opens = data.get("open") or []
    highs = data.get("high") or []
    lows = data.get("low") or []
    closes = data.get("close") or []
    vols = data.get("volume") or []
    n = min(len(ts), len(opens), len(highs), len(lows), len(closes))
    rows: list[dict[str, Any]] = []
    for i in range(n):
        t_ms = int(ts[i])
        dt = datetime.fromtimestamp(t_ms / 1000.0, tz=timezone.utc)
        rows.append(
            {
                "time": dt.isoformat(),
                "open": float(opens[i]),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "close": float(closes[i]),
                "volume": float(vols[i]) if i < len(vols) else 0.0,
            }
        )
    rows = [r for r in rows if r["open"] > 0 and r["high"] > 0 and r["low"] > 0 and r["close"] > 0]
    rows.sort(key=lambda x: x["time"])
    return rows


def _alltick_kline_type(interval: str) -> int:
    mapping = {"1m": 1, "5m": 2, "15m": 3, "30m": 4, "60m": 5, "1d": 8, "1w": 9, "1mo": 10}
    return mapping.get(interval, 8)


def _to_alltick_code(ticker: str, market: str) -> str:
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


def fetch_ohlcv_alltick(*, ticker: str, market: str, interval: str, limit: int) -> list[dict[str, Any]]:
    token = os.getenv("ALLTICK_TOKEN", "").strip()
    if not token:
        raise ValueError("ALLTICK_TOKEN 未设置")
    trace = f"stock-analysis-{int(datetime.now(timezone.utc).timestamp())}"
    query_obj = {
        "trace": trace,
        "data": {
            "code": _to_alltick_code(ticker=ticker, market=market),
            "kline_type": _alltick_kline_type(interval),
            "kline_timestamp_end": 0,
            "query_kline_num": max(30, min(int(limit), 500)),
            "adjust_type": 0,
        },
    }
    query = urlencode({"token": token, "query": json.dumps(query_obj, separators=(",", ":"))})
    url = f"https://quote.alltick.co/quote-stock-b-api/kline?{query}"
    payload = _http_get_json(url)
    if int(payload.get("ret", -1)) != 200:
        raise ValueError(f"AllTick 返回错误: {payload.get('msg') or payload}")
    kline_list = ((payload.get("data") or {}).get("kline_list")) or []
    rows: list[dict[str, Any]] = []
    for item in kline_list:
        t_sec = int(item["timestamp"])
        dt = datetime.fromtimestamp(t_sec, tz=timezone.utc)
        rows.append(
            {
                "time": dt.isoformat(),
                "open": float(item["open_price"]),
                "high": float(item["high_price"]),
                "low": float(item["low_price"]),
                "close": float(item["close_price"]),
                "volume": float(item.get("volume", 0.0)),
            }
        )
    rows = [r for r in rows if r["open"] > 0 and r["high"] > 0 and r["low"] > 0 and r["close"] > 0]
    rows.sort(key=lambda x: x["time"])
    return rows


def fetch_ohlcv(provider: str, ticker: str, market: str, interval: str, limit: int) -> list[dict[str, Any]]:
    p = (provider or "tickflow").strip().lower()
    if p == "akshare":
        return fetch_ohlcv_akshare(ticker=ticker, market=market, interval=interval, limit=limit)
    if p == "tickflow":
        return fetch_ohlcv_tickflow(ticker=ticker, market=market, interval=interval, limit=limit)
    if p == "alltick":
        return fetch_ohlcv_alltick(ticker=ticker, market=market, interval=interval, limit=limit)
    if p in {"goldapi", "gold-api", "gold_api"}:
        return fetch_ohlcv_goldapi(ticker=ticker, market=market, interval=interval, limit=limit)
    raise ValueError(f"暂不支持的 provider: {provider}（支持 akshare/tickflow/alltick/goldapi）")
