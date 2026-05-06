"""
台账写入策略：RR 门槛（对齐 CryptoTradeDesk 口径）与可选质量门。

配置来源：config/analysis_defaults.yaml 的 min_journal_rr 与 journal_quality。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any

from config.runtime_config import get_journal_quality, get_min_journal_rr
from analysis.kline_metrics import time_stop_deadline_utc
from analysis.trade_journal import to_iso_local

def reload_journal_policy_config() -> None:
    """兼容旧接口。配置由 runtime_config 统一读取，无需本地缓存刷新。"""
    return None


def min_journal_rr() -> float:
    return get_min_journal_rr(1.2)


def journal_quality() -> dict[str, Any]:
    return get_journal_quality()


def calc_journal_rr(idea: dict[str, Any]) -> float | None:
    """
    RR = |tp1 - entry| / |entry - stop|。
    entry 优先 entry_price，否则 entry_zone 中点（与 CryptoTradeDesk _calc_rr 一致）。
    """
    try:
        entry_zone = idea.get("entry_zone") or []
        entry_price = idea.get("entry_price")
        if isinstance(entry_price, (int, float)):
            entry = float(entry_price)
        elif isinstance(entry_zone, list) and len(entry_zone) == 2:
            entry = (float(entry_zone[0]) + float(entry_zone[1])) / 2.0
        else:
            return None
        stop = float(idea.get("stop_loss") or 0.0)
        tps = idea.get("take_profit_levels") or []
        if not (isinstance(tps, list) and tps):
            return None
        tp1 = float(tps[0])
    except (TypeError, ValueError):
        return None
    risk = abs(entry - stop)
    reward = abs(tp1 - entry)
    if risk <= 1e-12 or reward <= 1e-12:
        return None
    return reward / risk


def idea_passes_journal_append_gates(idea: dict[str, Any]) -> tuple[bool, str]:
    """写入 trade_journal.jsonl 前：RR 门槛 + 可选 journal_quality。"""
    rr = idea.get("rr")
    if rr is None:
        rr = calc_journal_rr(idea)
        if rr is not None:
            idea["rr"] = round(float(rr), 4)
    min_r = min_journal_rr()
    if rr is None or float(rr) < float(min_r) - 1e-12:
        return False, f"rr_below_min(rr={rr},min={min_r})"

    jq = journal_quality()
    if not jq.get("enabled"):
        return True, ""

    flags = idea.get("structure_filter_flags") or []
    if not isinstance(flags, list):
        flags = []
    flag_strs = [str(f) for f in flags]
    skip = jq.get("skip_structure_flags") or []
    if isinstance(skip, list):
        for s in skip:
            if s and str(s) in flag_strs:
                return False, f"flag_skip:{s}"

    if jq.get("require_mtf_align_for_crypto_only"):
        m = str(idea.get("market") or "").upper()
        prov = str(idea.get("provider") or "").lower()
        if m == "CRYPTO" or prov == "gateio":
            if idea.get("mtf_aligned") is not True:
                return False, "mtf_align_required"

    if jq.get("swing_require_wyckoff_aligned") and str(idea.get("plan_type") or "") == "swing":
        if idea.get("wyckoff_aligned") is not True:
            return False, "swing_wyckoff_align_required"

    return True, ""


def swing_journal_enabled() -> bool:
    """默认开启（与 Crypto 双轨对齐）；可在 journal_quality.swing_journal_enabled 设为 false 关闭。"""
    jq = journal_quality()
    if "swing_journal_enabled" in jq:
        return bool(jq.get("swing_journal_enabled"))
    return True


def _valid_until_utc(now_utc: datetime, interval: str) -> datetime:
    iv = (interval or "").lower()
    if iv in {"1d", "1w", "1m", "1mo"}:
        return now_utc + timedelta(days=3)
    if iv in {"4h", "1h"}:
        return now_utc + timedelta(hours=24)
    return now_utc + timedelta(hours=12)


def _interval_minutes(interval: str) -> int:
    m = {
        "1d": 1440,
        "1day": 1440,
        "4h": 240,
        "1h": 60,
        "60m": 60,
        "30m": 30,
        "15m": 15,
        "5m": 5,
        "1m": 1,
    }
    return int(m.get((interval or "").strip().lower(), 60))


def maybe_build_swing_journal_entry(
    *,
    now_utc: datetime,
    asset: dict[str, Any],
    provider: str,
    interval: str,
    stats: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    加密 / gateio：第二条「波段」候选（plan_type=swing），与 tactical 分槽共存。
    基于 MA 8/21/55 排列 + 较宽 entry_zone；初始 status=pending。
    """
    if not swing_journal_enabled():
        return None
    market_u = str(asset.get("market") or "").upper()
    prov = str(provider or "").lower()
    if market_u != "CRYPTO" and prov != "gateio":
        return None

    jq = journal_quality()
    min_bars = int(jq.get("swing_min_bars", 55) or 55)
    if len(rows) < min_bars:
        return None
    n_bars = int(stats.get("n_bars") or 0)
    if n_bars < min_bars:
        return None

    ma = stats.get("ma_system") or {}
    s8 = ma.get("sma_short")
    s21 = ma.get("sma_mid")
    s55 = ma.get("sma_long")
    if not all(isinstance(x, (int, float)) for x in (s8, s21, s55)):
        return None
    s8_f, s21_f, s55_f = float(s8), float(s21), float(s55)
    last = float(stats["last"])
    if last > s21_f > s55_f:
        direction = "long"
    elif last < s21_f < s55_f:
        direction = "short"
    else:
        return None

    wy = stats.get("wyckoff_123_v1") or {}
    wy_aligned = bool(wy.get("aligned"))
    bg = wy.get("background") or {}
    wyckoff_bias = str(bg.get("bias") or "neutral")

    half = max(abs(s8_f - s21_f) / 2.0, last * 0.002, abs(s21_f - s55_f) * 0.28)
    center = s21_f
    zl = center - half
    zh = center + half
    el = round(float(min(zl, zh)), 6)
    eh = round(float(max(zl, zh)), 6)
    entry_price = round((el + eh) / 2.0, 6)

    swing_high = stats.get("swing_high")
    swing_low = stats.get("swing_low")
    sh = float(swing_high) if isinstance(swing_high, (int, float)) else None
    sl = float(swing_low) if isinstance(swing_low, (int, float)) else None

    iv_l = (interval or "").lower()
    min_stop_pct = 0.008 if iv_l in {"1d", "1day"} else 0.012

    if direction == "long":
        stop_fb = min(entry_price * (1.0 - min_stop_pct), s55_f * 0.995)
        stop = stop_fb
        if sl is not None:
            stop = min(stop_fb, sl * 0.998)
        tp1 = sh if sh is not None and sh > entry_price else entry_price * 1.02
        tp2 = float(tp1) * 1.015 if float(tp1) > entry_price else entry_price * 1.03
    else:
        stop_fb = max(entry_price * (1.0 + min_stop_pct), s55_f * 1.005)
        stop = stop_fb
        if sh is not None:
            stop = max(stop_fb, sh * 1.002)
        tp1 = sl if sl is not None and sl < entry_price else entry_price * 0.98
        tp2 = float(tp1) * 0.985 if float(tp1) < entry_price else entry_price * 0.97

    risk = abs(entry_price - stop)
    reward = abs(float(tp1) - entry_price)
    if risk <= 1e-12 or reward <= 1e-12:
        return None
    rr = round(reward / risk, 4)

    fill_price = last
    order_kind_cn = "实时单" if el <= fill_price <= eh else "挂单"

    ts_v = stats.get("time_stop_v1") or {}
    bars = int(ts_v.get("max_wait_bars") or 5)
    ddl_iso = time_stop_deadline_utc(now_utc=now_utc, interval=interval, bars=bars)
    ddl_dt = datetime.fromisoformat(ddl_iso.replace("Z", "+00:00"))
    interval_min = _interval_minutes(interval)
    valid_until = max(_valid_until_utc(now_utc, interval), now_utc + timedelta(minutes=interval_min * 16))
    valid_until_local_iso = to_iso_local(valid_until)
    now_local_iso = to_iso_local(now_utc)

    mtf = stats.get("mtf_v1") or {}
    mtf_aligned: bool | None = mtf.get("aligned") if mtf.get("enabled") is True else None
    sf = stats.get("structure_filters_v1") or {}
    structure_flags = sf.get("flags") if isinstance(sf.get("flags"), list) else []

    stable_key = "|".join(
        [
            str(asset.get("symbol") or "UNKNOWN"),
            str(interval or "1d"),
            "swing",
            direction,
            now_utc.isoformat(),
        ]
    )
    idea_id = hashlib.sha1(stable_key.encode("utf-8")).hexdigest()[:12]

    regime = stats.get("market_regime") or {}
    reason = (
        f"{interval} swing·MA8/21/55 排列；Regime={str(regime.get('label') or '—')}；"
        f"威科夫aligned={'是' if wy_aligned else '否'}。"
    )

    lifecycle_v1: dict[str, Any] = {
        "version": "v1",
        "time_stop_rule": str(ts_v.get("rule") or ""),
        "time_stop_deadline_utc": to_iso_local(ddl_dt),
        "invalidation_hints": [
            "价格有效突破/跌破结构止损位",
            "超过 time_stop_deadline_utc 仍未触发或结构被推翻，应重评",
            "结构过滤 flags 含 low_liquidity_volume / abnormally_narrow_range 时降权",
        ],
    }

    raw_tags = asset.get("tags")
    tags_out: list[str] = []
    if isinstance(raw_tags, list):
        tags_out = [str(t).strip() for t in raw_tags if str(t).strip()]

    out: dict[str, Any] = {
        "idea_id": idea_id,
        "created_at_utc": now_local_iso,
        "symbol": asset["symbol"],
        "asset": asset.get("name") or asset["symbol"],
        "market": asset.get("market") or "UNK",
        "provider": provider,
        "interval": interval,
        "plan_type": "swing",
        "direction": direction,
        "entry_type": "limit",
        "order_kind_cn": order_kind_cn,
        "entry_zone": [el, eh],
        "entry_price": entry_price,
        "signal_last": round(fill_price, 6),
        "position_risk_pct": 0.5,
        "stop_loss": round(float(stop), 6),
        "take_profit_levels": [round(float(tp1), 6), round(float(tp2), 6)],
        "rr": rr,
        "strategy_reason": reason,
        "valid_until_utc": valid_until_local_iso,
        "status": "pending",
        "updated_at_utc": now_local_iso,
        "filled_at_utc": None,
        "fill_price": None,
        "exit_status": None,
        "closed_at_utc": None,
        "closed_price": None,
        "realized_pnl_pct": None,
        "risk_note": "仅技术分析演示，需结合风控独立决策。",
        "wyckoff_bias": wyckoff_bias,
        "wyckoff_aligned": wy_aligned,
        "mtf_aligned": mtf_aligned,
        "structure_filter_flags": structure_flags,
        "time_stop_deadline_utc": to_iso_local(ddl_dt),
        "lifecycle_v1": lifecycle_v1,
    }
    if tags_out:
        out["tags"] = tags_out
    return out
