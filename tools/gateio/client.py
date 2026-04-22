from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tools.common.errors import ParseError, ProviderError, RateLimitError


def _http_get_json(url: str, timeout: float = 30.0) -> Any:
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Stock_Analysis/1.0 (+https://github.com)",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        msg = str(exc).lower()
        if "429" in msg or "rate" in msg:
            raise RateLimitError(f"gateio 限频: {exc}") from exc
        raise ProviderError(f"gateio 请求失败: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError(f"gateio 返回非 JSON: {raw[:240]!r}") from exc


def normalize_interval(interval: str) -> str:
    iv = str(interval or "1d").strip().lower()
    iv_map = {"1w": "7d", "1wk": "7d", "1mo": "30d"}
    iv = iv_map.get(iv, iv)
    allowed = {"10s", "1m", "5m", "15m", "30m", "1h", "4h", "8h", "1d", "7d", "30d"}
    if iv not in allowed:
        raise ValueError(f"gateio 不支持的 interval: {interval}")
    return iv


def fetch_ohlcv_gateio(*, pair: str, interval: str, limit: int) -> list[dict[str, Any]]:
    iv = normalize_interval(interval)
    lim = max(30, min(int(limit), 1000))
    query = urlencode({"currency_pair": pair, "interval": iv, "limit": str(lim)})
    url = f"https://api.gateio.ws/api/v4/spot/candlesticks?{query}"
    data = _http_get_json(url)
    if not isinstance(data, list):
        raise ParseError(f"gateio 返回异常: {data!r}")
    rows: list[dict[str, Any]] = []
    for c in data:
        if not isinstance(c, list) or len(c) < 7:
            continue
        try:
            ts_sec = int(c[0])
            dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
            rows.append(
                {
                    "time": dt.isoformat(),
                    "open": float(c[5]),
                    "high": float(c[3]),
                    "low": float(c[4]),
                    "close": float(c[2]),
                    "volume": float(c[6]),
                }
            )
        except (TypeError, ValueError):
            continue
    rows = [r for r in rows if r["open"] > 0 and r["high"] > 0 and r["low"] > 0 and r["close"] > 0]
    rows.sort(key=lambda x: x["time"])
    return rows[-lim:]
