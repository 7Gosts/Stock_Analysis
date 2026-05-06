"""
Gold API：品种列表、贵金属 OHLCV。

默认按日线拉 K 线（``/api/v1/kline`` + ``period=1440``），失败再回退 ``/gold/history``。
鉴权：``appkey`` / ``apikey``（见 ``tools.goldapi.client``）。基址 ``GOLD_API_BASE``，密钥 ``GOLD_API_APPKEY``。
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from tools.common.errors import ParseError, ProviderError, RateLimitError
from tools.goldapi.client import fetch_history, fetch_kline, fetch_varieties

_VARIETIES_CACHE: list[dict[str, Any]] | None = None

# 项目内默认 Gold API appkey（可被 GOLD_API_APPKEY / GOLD_API_KEY 覆盖）
_DEFAULT_GOLD_API_APPKEY = "FFIKVPEL2LH9F9KGM_E3"


def gold_api_base() -> str:
    return (os.getenv("GOLD_API_BASE") or "https://gold-api.cn").rstrip("/")


def gold_api_appkey() -> str:
    return (
        os.getenv("GOLD_API_APPKEY", "").strip()
        or os.getenv("GOLD_API_KEY", "").strip()
        or _DEFAULT_GOLD_API_APPKEY
    ).strip()


def fetch_gold_varieties() -> list[dict[str, Any]]:
    """GET /api/v1/gold/varieties（无需 appkey）。"""
    return fetch_varieties(base_url=gold_api_base())


def _get_varieties_cached() -> list[dict[str, Any]]:
    global _VARIETIES_CACHE
    if _VARIETIES_CACHE is None:
        _VARIETIES_CACHE = fetch_gold_varieties()
    return _VARIETIES_CACHE


# 日线 K：period 为分钟，厂商约定 1440 = 1 日。
GOLDAPI_PERIOD_DAILY_MINUTES = "1440"


def resolve_gold_id(ticker: str) -> str:
    """
    将 data_symbol 解析为接口 goldid：
    - 已是 goldId（如 1053、hf_XAU、nf_AU0）则原样返回；
    - 否则按品种代码（如 Au9999、AuT+D）在 varieties 里匹配 `variety` 字段（大小写不敏感）。
    """
    raw = ticker.strip()
    if not raw:
        raise ValueError("贵金属 ticker 为空")
    if raw.isdigit() or raw.startswith(("hf_", "nf_")):
        return raw
    key = raw.upper().replace("＋", "+")
    for row in _get_varieties_cached():
        v = str(row.get("variety") or "").strip().upper()
        if v == key:
            gid = str(row.get("goldId") or "").strip()
            if gid:
                return gid
    raise ValueError(f"未找到贵金属品种映射: {ticker!r}（请使用 gold-api 控制台 goldid，如 1053 / hf_XAU）")


def resolve_kline_symbol(ticker: str) -> str:
    """K 线 ``symbol``：品种代码或从 goldId 反查 variety。"""
    raw = ticker.strip()
    if not raw:
        raise ValueError("贵金属 ticker 为空")
    if raw.isdigit() or raw.startswith(("hf_", "nf_")):
        gid = raw
        for row in _get_varieties_cached():
            if str(row.get("goldId") or "").strip() == gid:
                v = str(row.get("variety") or "").strip()
                if v:
                    return v
        raise ValueError(f"无法将 goldId {gid!r} 映射为 K 线 symbol（请检查品种表）")
    return raw


def gold_api_kline_url() -> str:
    """日线 K 默认 ``{GOLD_API_BASE}/api/v1/kline``；可用 ``GOLD_API_KLINE_URL`` 覆盖为完整 URL。"""
    u = (os.getenv("GOLD_API_KLINE_URL") or "").strip().rstrip("/")
    if u:
        return u
    return f"{gold_api_base()}/api/v1/kline"


def _parse_dt_any(s: str) -> datetime:
    """解析 API 返回的时间字符串（含 `yyyy-MM-dd HH:MM:SS` 或仅日期）。"""
    raw = (s or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        n = 19 if "H" in fmt else 10
        try:
            return datetime.strptime(raw[:n], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    day = raw[:10].replace("/", "-")
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _row_from_item(it: dict[str, Any]) -> dict[str, Any] | None:
    """从单条历史记录抽取 OHLCV（兼容多种字段命名）。"""
    if not isinstance(it, dict):
        return None
    date_keys = (
        "timestamp",
        "businessDate",
        "bizDate",
        "tradeDate",
        "date",
        "dt",
        "datetime",
        "pubDate",
        "updateTime",
    )
    dt_raw = None
    for k in date_keys:
        if it.get(k):
            dt_raw = str(it[k])
            break
    if not dt_raw:
        return None
    try:
        ts = _parse_dt_any(dt_raw)
    except Exception:
        return None

    def pick_float(*keys: str) -> float | None:
        for k in keys:
            v = it.get(k)
            if v is None or v == "":
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return None

    close = pick_float("close", "closePrice", "lastPrice", "last", "settle", "settlementPrice", "price", "value")
    open_ = pick_float("open", "openPrice", "openingPrice")
    high = pick_float("high", "highPrice", "maxPrice", "highPx")
    low = pick_float("low", "lowPrice", "minPrice", "lowPx")
    vol = pick_float("volume", "vol", "tradeAmount", "turnover", "amount")
    if close is None:
        return None
    if open_ is None:
        open_ = close
    if high is None:
        high = max(open_, close)
    if low is None:
        low = min(open_, close)
    if vol is None:
        vol = 0.0
    return {
        "time": ts.isoformat(),
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": float(vol),
    }


def _rollup_to_daily_bars(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将同一日历日的多根 bar（如小时线）合并为一根日线 OHLCV。"""
    if not rows:
        return []

    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        day = str(r.get("time") or "")[:10]
        if len(day) == 10:
            by_day[day].append(r)
    out: list[dict[str, Any]] = []
    for day in sorted(by_day.keys()):
        xs = sorted(by_day[day], key=lambda x: str(x.get("time") or ""))
        o = float(xs[0]["open"])
        c = float(xs[-1]["close"])
        h = max(float(x["high"]) for x in xs)
        lo = min(float(x["low"]) for x in xs)
        v = sum(float(x.get("volume") or 0.0) for x in xs)
        ts = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
        out.append(
            {
                "time": ts.isoformat(),
                "open": o,
                "high": h,
                "low": lo,
                "close": c,
                "volume": v,
            }
        )
    return out


def _rows_from_history_result(result: Any) -> list[dict[str, Any]]:
    """解析 /gold/history 的 result 字段。"""
    if result is None:
        return []
    if isinstance(result, list):
        candidates = result
    elif isinstance(result, dict):
        for k in ("list", "rows", "records", "data", "items", "points", "dtList"):
            v = result.get(k)
            if isinstance(v, list):
                candidates = v
                break
        else:
            # dtList 为 dict（按 goldId 分组）时取第一个非空 list
            dt = result.get("dtList")
            if isinstance(dt, dict):
                for _k, v in dt.items():
                    if isinstance(v, list) and v:
                        candidates = v
                        break
                else:
                    candidates = []
            else:
                candidates = []
    else:
        return []

    out: list[dict[str, Any]] = []
    for it in candidates:
        if isinstance(it, dict):
            row = _row_from_item(it)
            if row:
                out.append(row)
    return out


def _finalize_gold_rows(rows: list[dict[str, Any]], *, lim: int) -> list[dict[str, Any]]:
    rows = [r for r in rows if r["open"] > 0 and r["high"] > 0 and r["low"] > 0 and r["close"] > 0]
    rows = _rollup_to_daily_bars(rows)
    rows.sort(key=lambda x: x["time"])
    if len(rows) < 30:
        raise ValueError(
            f"goldapi 返回有效 K 线不足 30 根（实际 {len(rows)}），请检查 goldid、日期区间或套餐额度"
        )
    return rows[-lim:]


def fetch_ohlcv_goldapi(*, ticker: str, market: str, interval: str, limit: int) -> list[dict[str, Any]]:
    """
    贵金属日线 OHLCV：默认 ``GET {GOLD_API_BASE}/api/v1/kline`` + ``period=1440``；
    失败则回退 ``/gold/history``（细粒度再按日聚合）。

    ticker：``data_symbol``（Au9999）或 goldId（1053）；interval 仅 ``1d``。
    """
    _ = market
    iv = (interval or "1d").lower()
    if iv not in {"1d", "1day"}:
        raise ValueError("goldapi 暂仅支持日线 interval=1d（历史接口按日期）")

    appkey = gold_api_appkey()
    if not appkey:
        raise ValueError("goldapi 缺少 appkey：请设置 GOLD_API_APPKEY 或在 gold_api.py 配置默认 key")

    gold_id = resolve_gold_id(ticker)
    sym = resolve_kline_symbol(ticker)
    lim = max(30, min(int(limit), 5000))
    period = (os.getenv("GOLD_API_KLINE_PERIOD") or GOLDAPI_PERIOD_DAILY_MINUTES).strip() or GOLDAPI_PERIOD_DAILY_MINUTES
    kline_limit = min(max(lim + 50, 120), 5000)

    try:
        payload = fetch_kline(
            url=gold_api_kline_url(),
            symbol=sym,
            period=period,
            limit=kline_limit,
            auth_key=appkey,
        )
        rows = _rows_from_history_result(payload)
        return _finalize_gold_rows(rows, lim=lim)
    except (ProviderError, ParseError, RateLimitError, ValueError):
        pass

    span_days = min(int(lim * 2.2) + 120, 4000)
    end_d = datetime.now(timezone.utc).date()
    start_d = end_d - timedelta(days=span_days)
    result = fetch_history(
        base_url=gold_api_base(),
        appkey=appkey,
        gold_id=gold_id,
        start_date=start_d.isoformat(),
        end_date=end_d.isoformat(),
        limit=min(max(lim + 200, 400), 5000),
    )
    rows = _rows_from_history_result(result)
    return _finalize_gold_rows(rows, lim=lim)
