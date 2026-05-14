"""
Gold API：品种列表、贵金属 OHLCV。

按官方文档使用 ``/api/v1/gold/varieties`` 与 ``/api/v1/gold/history``。
鉴权使用 ``appkey``；AU9999 等品种先映射为 ``goldid``，再按历史 OHLC 数据归一化为项目内部 K 线。
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any
from tools.common.errors import ProviderError
from tools.goldapi.client import fetch_history, fetch_varieties

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


# 官方 history 文档当前可稳定支撑的归一化周期。
GOLDAPI_SUPPORTED_INTERVALS: tuple[str, ...] = ("1h", "4h", "1d", "1day")


def _normalize_gold_interval(interval: str) -> str:
    iv = (interval or "1d").strip().lower()
    if iv == "1day":
        return "1d"
    return iv


def normalize_gold_history_interval(interval: str) -> str:
    """将标准 interval 归一化到官方 history 方案支持的聚合粒度。"""
    iv = _normalize_gold_interval(interval)
    if iv not in {"1h", "4h", "1d"}:
        allowed = ", ".join(sorted({k for k in GOLDAPI_SUPPORTED_INTERVALS if k != "1day"}))
        raise ValueError(f"goldapi 不支持的 interval={interval!r}（支持: {allowed}）")
    return iv


def resolve_gold_id(ticker: str) -> str:
    """将 data_symbol 解析为官方 history 接口需要的 goldid。"""
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
    raise ValueError(f"未找到贵金属品种映射: {ticker!r}（请使用官方品种代码，如 1053 / hf_XAU）")


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
    """从官方 history 单条记录抽取 OHLCV（兼容字段别名）。"""
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
    """解析 GoldAPI 返回的 result 字段。"""
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


def _floor_to_4h(dt: datetime) -> datetime:
    return dt.replace(hour=(dt.hour // 4) * 4, minute=0, second=0, microsecond=0)


def _aggregate_rows(rows: list[dict[str, Any]], *, interval: str) -> list[dict[str, Any]]:
    if interval == "1h":
        out = list(rows)
        out.sort(key=lambda x: x["time"])
        return out

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        dt = datetime.fromisoformat(str(row.get("time") or ""))
        bucket_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0) if interval == "1d" else _floor_to_4h(dt)
        buckets[bucket_dt.isoformat()].append(row)

    out: list[dict[str, Any]] = []
    for key in sorted(buckets.keys()):
        xs = sorted(buckets[key], key=lambda x: str(x.get("time") or ""))
        out.append(
            {
                "time": key,
                "open": float(xs[0]["open"]),
                "high": max(float(x["high"]) for x in xs),
                "low": min(float(x["low"]) for x in xs),
                "close": float(xs[-1]["close"]),
                "volume": sum(float(x.get("volume") or 0.0) for x in xs),
            }
        )
    return out


def _history_span_days(interval: str, limit: int) -> int:
    if interval == "1h":
        return min(max(int(limit / 6) + 20, 30), 4000)
    if interval == "4h":
        return min(max(int(limit / 3) + 45, 60), 4000)
    return min(max(int(limit * 2.5) + 30, 120), 4000)


def _history_fetch_limit(interval: str, limit: int) -> int:
    if interval == "1h":
        factor = 4
    elif interval == "4h":
        factor = 8
    else:
        factor = 16
    return min(max(int(limit) * factor, 400), 5000)


def _finalize_gold_rows(
    rows: list[dict[str, Any]], *, lim: int, interval: str
) -> list[dict[str, Any]]:
    rows = [r for r in rows if r["open"] > 0 and r["high"] > 0 and r["low"] > 0 and r["close"] > 0]
    rows = _aggregate_rows(rows, interval=interval)
    rows.sort(key=lambda x: x["time"])
    if len(rows) < 30:
        raise ValueError(
            f"goldapi 返回有效 K 线不足 30 根（实际 {len(rows)}），请检查 goldid、日期区间或套餐额度"
        )
    return rows[-lim:]


def fetch_ohlcv_goldapi(*, ticker: str, market: str, interval: str, limit: int) -> list[dict[str, Any]]:
    """
    贵金属 OHLCV：按官方 ``/api/v1/gold/history`` 拉取 ``goldid`` 的历史数据，再归一化为项目所需 K 线。

    - 当前稳定支持 ``1h`` / ``4h`` / ``1d``（及 ``1day``）。
    - ``goldid`` 通过 ``/api/v1/gold/varieties`` 从 ``Au9999`` 等品种代码映射得到。
    """
    _ = market
    iv = normalize_gold_history_interval(interval)

    appkey = gold_api_appkey()
    if not appkey:
        raise ValueError("goldapi 缺少 appkey：请设置 GOLD_API_APPKEY 或在 gold_api.py 配置默认 key")

    gold_id = resolve_gold_id(ticker)
    lim = max(30, min(int(limit), 5000))
    try:
        end_d = datetime.now(timezone.utc).date()
        start_d = end_d - timedelta(days=_history_span_days(iv, lim))
        result = fetch_history(
            base_url=gold_api_base(),
            appkey=appkey,
            gold_id=gold_id,
            start_date=start_d.isoformat(),
            end_date=end_d.isoformat(),
            limit=_history_fetch_limit(iv, lim),
        )
        rows = _rows_from_history_result(result)
        if rows:
            return _finalize_gold_rows(rows, lim=lim, interval=iv)
    except (ProviderError, ValueError) as exc:
        raise ProviderError(f"goldapi history 失败（interval={iv}, goldid={gold_id}）: {exc}") from exc

    raise ProviderError(f"goldapi history 返回空数据（interval={iv}, goldid={gold_id}）")
