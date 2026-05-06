from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tools.common.errors import ParseError, ProviderError, RateLimitError


def http_get_json(url: str, *, timeout: float = 45.0) -> Any:
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
            raise RateLimitError(f"goldapi 限频: {exc}") from exc
        raise ProviderError(f"goldapi 请求失败: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParseError(f"goldapi 返回非 JSON: {raw[:240]!r}") from exc


def fetch_varieties(*, base_url: str) -> list[dict[str, Any]]:
    url = f"{base_url}/api/v1/gold/varieties"
    payload = http_get_json(url)
    if str(payload.get("success")) != "1":
        raise ProviderError(f"gold varieties 失败: {payload}")
    result = payload.get("result")
    if not isinstance(result, list):
        return []
    return result


def _kline_auth_query_name(url: str) -> str:
    return "apikey" if "api.gold-api.cn" in (url or "").lower() else "appkey"


def fetch_kline(*, url: str, symbol: str, period: str, limit: int, auth_key: str) -> Any:
    """GET K 线：symbol + period（分钟，日线 1440）+ limit + 鉴权。"""
    qn = _kline_auth_query_name(url)
    params = {
        "symbol": symbol,
        "period": str(period),
        "limit": str(int(limit)),
        qn: auth_key,
    }
    full = f"{url.rstrip('/')}?{urlencode(params)}"
    payload = http_get_json(full)
    if isinstance(payload, dict) and str(payload.get("success")) == "0":
        raise ProviderError(
            f"gold kline 失败: {payload.get('msgId')} {payload.get('msg')} body={payload}"
        )
    return payload


def fetch_history(*, base_url: str, appkey: str, gold_id: str, start_date: str, end_date: str, limit: int) -> Any:
    params = {
        "goldid": gold_id,
        "start_date": start_date,
        "end_date": end_date,
        "limit": str(limit),
        "appkey": appkey,
    }
    url = f"{base_url}/api/v1/gold/history?{urlencode(params)}"
    payload = http_get_json(url)
    if str(payload.get("success")) != "1":
        raise ProviderError(f"gold history 失败: {payload.get('msgId')} {payload.get('msg')} body={payload}")
    return payload.get("result")
