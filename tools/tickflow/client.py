from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tools.common.errors import ParseError, ProviderError, RateLimitError


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
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        msg = str(exc).lower()
        if "429" in msg or "rate" in msg:
            raise RateLimitError(f"tickflow 限频: {exc}") from exc
        raise ProviderError(f"tickflow 请求失败: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError(f"tickflow 返回非 JSON: {raw[:240]!r}") from exc


def fetch_ohlcv_tickflow(*, symbol: str, interval: str, limit: int) -> list[dict[str, Any]]:
    api_key = os.getenv("TICKFLOW_API_KEY", "").strip()
    base = "https://api.tickflow.org" if api_key else "https://free-api.tickflow.org"
    period = interval if interval in {"1d", "1w", "1M", "1Q", "1Y"} else "1d"
    query = urlencode(
        {
            "symbol": symbol,
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
        try:
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
        except (TypeError, ValueError):
            continue
    rows = [r for r in rows if r["open"] > 0 and r["high"] > 0 and r["low"] > 0 and r["close"] > 0]
    rows.sort(key=lambda x: x["time"])
    return rows
