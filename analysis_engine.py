from __future__ import annotations

from typing import Any


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _sma(values: list[float], n: int) -> float | None:
    if n <= 0 or len(values) < n:
        return None
    return sum(values[-n:]) / n


def _pct(a: float, b: float) -> float | None:
    if b == 0:
        return None
    return (a - b) / b * 100.0


def _fib_levels(anchor_low: float, anchor_high: float) -> dict[str, float]:
    span = anchor_high - anchor_low
    return {
        "0.0": anchor_low,
        "0.236": anchor_low + span * 0.236,
        "0.382": anchor_low + span * 0.382,
        "0.5": anchor_low + span * 0.5,
        "0.618": anchor_low + span * 0.618,
        "0.786": anchor_low + span * 0.786,
        "1.0": anchor_high,
    }


def _price_vs_fib_zone(last: float, fib: dict[str, float]) -> str:
    order = ["0.0", "0.236", "0.382", "0.5", "0.618", "0.786", "1.0"]
    vals = [fib[k] for k in order]
    if last < vals[0]:
        return "below_0.0"
    if last > vals[-1]:
        return "above_1.0"
    for i in range(len(vals) - 1):
        if vals[i] <= last <= vals[i + 1]:
            return f"{order[i]}~{order[i + 1]}"
    return "unknown"


def _fmt_px(v: float) -> str:
    if abs(v) >= 1000:
        return f"{v:,.2f}"
    if abs(v) >= 1:
        return f"{v:.2f}"
    return f"{v:.4f}"


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _pivot_lows(lows: list[float], left: int = 2, right: int = 2) -> list[int]:
    idxs: list[int] = []
    n = len(lows)
    if n < left + right + 1:
        return idxs
    for i in range(left, n - right):
        ok = True
        for j in range(i - left, i):
            if lows[j] <= lows[i]:
                ok = False
                break
        if not ok:
            continue
        for j in range(i + 1, i + right + 1):
            if lows[j] < lows[i]:
                ok = False
                break
        if ok:
            idxs.append(i)
    return idxs


def _pivot_highs(highs: list[float], left: int = 2, right: int = 2) -> list[int]:
    idxs: list[int] = []
    n = len(highs)
    if n < left + right + 1:
        return idxs
    for i in range(left, n - right):
        ok = True
        for j in range(i - left, i):
            if highs[j] >= highs[i]:
                ok = False
                break
        if not ok:
            continue
        for j in range(i + 1, i + right + 1):
            if highs[j] > highs[i]:
                ok = False
                break
        if ok:
            idxs.append(i)
    return idxs


def compute_wyckoff_context(rows: list[dict[str, Any]], trend: str) -> dict[str, Any]:
    closes = [float(r["close"]) for r in rows if r.get("close") is not None]
    highs = [float(r["high"]) for r in rows if r.get("high") is not None]
    lows = [float(r["low"]) for r in rows if r.get("low") is not None]
    vols = [float(r.get("volume", 0.0) or 0.0) for r in rows]
    n = min(len(closes), len(highs), len(lows), len(vols))
    if n < 25:
        return {
            "bias": "neutral",
            "state": "insufficient_data",
            "effort_result": "unknown",
            "volume_ratio": None,
            "spread_ratio": None,
            "close_pos": None,
        }

    closes = closes[-n:]
    highs = highs[-n:]
    lows = lows[-n:]
    vols = vols[-n:]

    spreads = [max(0.0, highs[i] - lows[i]) for i in range(n)]
    last_spread = spreads[-1]
    spread_base = _avg(spreads[-21:-1]) if n >= 22 else _avg(spreads[:-1])
    last_vol = vols[-1]
    vol_base = _avg(vols[-21:-1]) if n >= 22 else _avg(vols[:-1])
    volume_ratio = (last_vol / vol_base) if vol_base and vol_base > 0 else None
    spread_ratio = (last_spread / spread_base) if spread_base and spread_base > 0 else None

    prev_close = closes[-2]
    last_close = closes[-1]
    ret1_pct = _pct(last_close, prev_close)
    bar_span = max(1e-9, highs[-1] - lows[-1])
    close_pos = _clamp((last_close - lows[-1]) / bar_span, 0.0, 1.0)

    vol_state = "normal_volume"
    if volume_ratio is not None:
        if volume_ratio >= 1.5:
            vol_state = "high_volume"
        elif volume_ratio <= 0.7:
            vol_state = "low_volume"

    spread_state = "normal_spread"
    if spread_ratio is not None:
        if spread_ratio >= 1.4:
            spread_state = "wide_spread"
        elif spread_ratio <= 0.7:
            spread_state = "narrow_spread"

    effort_result = "balanced"
    if vol_state == "high_volume" and spread_state == "wide_spread":
        if ret1_pct is not None and ret1_pct > 0 and close_pos >= 0.65:
            effort_result = "bullish_expansion"
        elif ret1_pct is not None and ret1_pct < 0 and close_pos <= 0.35:
            effort_result = "bearish_expansion"
        else:
            effort_result = "high_effort_absorption"
    elif vol_state == "low_volume" and spread_state == "narrow_spread":
        if ret1_pct is not None and ret1_pct >= 0:
            effort_result = "no_supply_like"
        else:
            effort_result = "no_demand_like"

    bias = "neutral"
    if effort_result in {"bullish_expansion", "no_supply_like"}:
        bias = "long_only"
    elif effort_result in {"bearish_expansion", "no_demand_like"}:
        bias = "short_only"
    elif effort_result == "high_effort_absorption":
        if "偏多" in trend:
            bias = "long_only"
        elif "偏空" in trend:
            bias = "short_only"

    state = f"{vol_state}|{spread_state}"
    return {
        "bias": bias,
        "state": state,
        "effort_result": effort_result,
        "volume_ratio": volume_ratio,
        "spread_ratio": spread_ratio,
        "close_pos": close_pos,
    }


def detect_123_setups(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    closes = [float(r["close"]) for r in rows if r.get("close") is not None]
    highs = [float(r["high"]) for r in rows if r.get("high") is not None]
    lows = [float(r["low"]) for r in rows if r.get("low") is not None]
    times = [str(r["time"]) for r in rows if r.get("time")]
    n = min(len(closes), len(highs), len(lows), len(times))
    if n < 30:
        return {"long": None, "short": None}

    closes = closes[-n:]
    highs = highs[-n:]
    lows = lows[-n:]
    times = times[-n:]
    piv_l = _pivot_lows(lows, left=2, right=2)
    piv_h = _pivot_highs(highs, left=2, right=2)

    long_setup: dict[str, Any] | None = None
    for p2 in reversed(piv_h):
        lows_before = [x for x in piv_l if x < p2]
        lows_after = [x for x in piv_l if x > p2]
        if not lows_before or not lows_after:
            continue
        p1 = lows_before[-1]
        p3 = lows_after[0]
        if p3 - p2 > 25:
            continue
        if not (lows[p3] > lows[p1]):
            continue
        entry = highs[p2]
        stop = lows[p3] * 0.998
        risk = entry - stop
        if risk <= 0:
            continue
        last_close = closes[-1]
        long_setup = {
            "side": "long",
            "triggered": last_close > entry,
            "entry": entry,
            "stop": stop,
            "tp1": entry + 1.5 * risk,
            "tp2": entry + 2.5 * risk,
            "risk_reward_tp1": 1.5,
            "risk_reward_tp2": 2.5,
            "p1": {"idx": p1, "price": lows[p1], "time": times[p1]},
            "p2": {"idx": p2, "price": highs[p2], "time": times[p2]},
            "p3": {"idx": p3, "price": lows[p3], "time": times[p3]},
        }
        break

    short_setup: dict[str, Any] | None = None
    for p2 in reversed(piv_l):
        highs_before = [x for x in piv_h if x < p2]
        highs_after = [x for x in piv_h if x > p2]
        if not highs_before or not highs_after:
            continue
        p1 = highs_before[-1]
        p3 = highs_after[0]
        if p3 - p2 > 25:
            continue
        if not (highs[p3] < highs[p1]):
            continue
        entry = lows[p2]
        stop = highs[p3] * 1.002
        risk = stop - entry
        if risk <= 0:
            continue
        last_close = closes[-1]
        short_setup = {
            "side": "short",
            "triggered": last_close < entry,
            "entry": entry,
            "stop": stop,
            "tp1": entry - 1.5 * risk,
            "tp2": entry - 2.5 * risk,
            "risk_reward_tp1": 1.5,
            "risk_reward_tp2": 2.5,
            "p1": {"idx": p1, "price": highs[p1], "time": times[p1]},
            "p2": {"idx": p2, "price": lows[p2], "time": times[p2]},
            "p3": {"idx": p3, "price": highs[p3], "time": times[p3]},
        }
        break

    return {"long": long_setup, "short": short_setup}


def build_wyckoff_123_plan(rows: list[dict[str, Any]], trend: str) -> dict[str, Any]:
    bg = compute_wyckoff_context(rows, trend=trend)
    setups = detect_123_setups(rows)
    preferred_side: str | None = None
    if bg["bias"] == "long_only":
        preferred_side = "long"
    elif bg["bias"] == "short_only":
        preferred_side = "short"

    selected = setups.get(preferred_side) if preferred_side else None
    aligned = selected is not None
    if not selected:
        # neutral 背景下，不主动给方向；但保留检测结果供排查
        selected = None

    return {
        "version": "v1",
        "background": bg,
        "setups": setups,
        "preferred_side": preferred_side,
        "selected_setup": selected,
        "aligned": aligned,
        "note": "仅技术分析与程序化演示，不构成投资建议。",
    }


def compute_ohlc_stats(rows: list[dict[str, Any]], *, interval: str) -> dict[str, Any] | None:
    closes = [float(r["close"]) for r in rows if r.get("close") is not None]
    highs = [float(r["high"]) for r in rows if r.get("high") is not None]
    lows = [float(r["low"]) for r in rows if r.get("low") is not None]
    times = [str(r["time"]) for r in rows if r.get("time")]
    n = len(closes)
    if n < 30:
        return None

    last = closes[-1]
    sma20 = _sma(closes, 20)
    sma60 = _sma(closes, 60)
    ret1 = _pct(last, closes[-2]) if n >= 2 else None
    ret5 = _pct(last, closes[-6]) if n >= 6 else None

    lookback = min(60, n)
    h_window = highs[-lookback:]
    l_window = lows[-lookback:]
    t_window = times[-lookback:]
    hi_idx = max(range(lookback), key=lambda i: h_window[i])
    lo_idx = min(range(lookback), key=lambda i: l_window[i])
    swing_high = h_window[hi_idx]
    swing_low = l_window[lo_idx]
    fib = _fib_levels(swing_low, swing_high) if swing_high > swing_low else {}
    fib_zone = _price_vs_fib_zone(last, fib) if fib else "unknown"

    trend = "震荡"
    if sma20 and sma60:
        if last > sma20 > sma60:
            trend = "偏多"
        elif last < sma20 < sma60:
            trend = "偏空"
        elif last >= sma20 and last < sma60:
            trend = "震荡偏空"
        elif last <= sma20 and last > sma60:
            trend = "震荡偏多"
    elif sma20:
        trend = "偏多" if last > sma20 else "偏空"

    wyckoff_123 = build_wyckoff_123_plan(rows, trend=trend)
    return {
        "interval": interval,
        "last": last,
        "sma20": sma20,
        "sma60": sma60,
        "ret1_pct": ret1,
        "ret5_pct": ret5,
        "swing_high": swing_high,
        "swing_low": swing_low,
        "swing_high_time": t_window[hi_idx],
        "swing_low_time": t_window[lo_idx],
        "fib_levels": fib,
        "price_vs_fib_zone": fib_zone,
        "trend": trend,
        "n_bars": n,
        "wyckoff_123_v1": wyckoff_123,
    }


def format_report_card(asset: dict[str, str], stats: dict[str, Any], research: dict[str, Any] | None = None) -> str:
    symbol = asset["symbol"]
    name = asset.get("name") or symbol
    market = asset.get("market") or "UNK"
    last = _fmt_px(float(stats["last"]))
    ret1 = stats.get("ret1_pct")
    ret5 = stats.get("ret5_pct")
    sma20 = stats.get("sma20")
    sma60 = stats.get("sma60")
    fib = stats.get("fib_levels") or {}
    strategy = stats.get("wyckoff_123_v1") or {}
    bg = strategy.get("background") or {}
    selected = strategy.get("selected_setup")

    def _fmt_pct(x: float | None) -> str:
        return "N/A" if x is None else f"{x:.2f}%"

    lines: list[str] = []
    lines.append(f"## {name}（{symbol}｜{market}）\n")
    lines.append(f"- **综合倾向**：{stats.get('trend', 'N/A')}\n")
    lines.append(
        f"- **现价**：{last}；1根涨跌：{_fmt_pct(ret1)}；5根涨跌：{_fmt_pct(ret5)}\n"
    )
    lines.append(
        f"- **均线结构**：SMA20={_fmt_px(sma20) if sma20 else 'N/A'}，SMA60={_fmt_px(sma60) if sma60 else 'N/A'}\n"
    )
    lines.append(
        f"- **近端结构范围**：低点 {_fmt_px(stats['swing_low'])}（{stats['swing_low_time']}）"
        f" -> 高点 {_fmt_px(stats['swing_high'])}（{stats['swing_high_time']}）\n"
    )
    if fib:
        lines.append(
            f"- **Fib区间**：现价位于 `{stats.get('price_vs_fib_zone', 'unknown')}`；"
            f"0.382={_fmt_px(fib['0.382'])}，0.5={_fmt_px(fib['0.5'])}，0.618={_fmt_px(fib['0.618'])}\n"
        )
    if research and isinstance(research, dict):
        items = research.get("items") or []
        total = research.get("total")
        kw = str(research.get("keyword") or "").strip()
        if items:
            lines.append(
                "- **研报参考（yanbaoke 搜索）**："
                f"关键词 `{kw}`；命中 {len(items)} 条"
                + (f"（接口 total={total}）" if isinstance(total, int) else "")
                + "\n"
            )
            for it in items[:3]:
                title = str(it.get("title") or "").strip()
                url = str(it.get("url") or "").strip()
                org = str(it.get("org_name") or "").strip()
                tail = f"（{org}）" if org else ""
                if title and url:
                    lines.append(f"  - [{title}]({url}){tail}\n")
                elif title:
                    lines.append(f"  - {title}{tail}\n")
    lines.append(
        "- **威科夫背景过滤（v1）**："
        f"bias={bg.get('bias', 'neutral')}，state={bg.get('state', 'N/A')}，"
        f"effort/result={bg.get('effort_result', 'unknown')}，"
        f"vol_ratio={_fmt_pct(bg.get('volume_ratio') * 100 - 100) if bg.get('volume_ratio') is not None else 'N/A'}，"
        f"spread_ratio={_fmt_pct(bg.get('spread_ratio') * 100 - 100) if bg.get('spread_ratio') is not None else 'N/A'}\n"
    )
    if selected:
        lines.append(
            f"- **123入场（{selected.get('side', 'N/A')}）**："
            f"P1={_fmt_px(float(selected['p1']['price']))}，"
            f"P2={_fmt_px(float(selected['p2']['price']))}，"
            f"P3={_fmt_px(float(selected['p3']['price']))}；"
            f"触发价={_fmt_px(float(selected['entry']))}，"
            f"止损={_fmt_px(float(selected['stop']))}，"
            f"TP1={_fmt_px(float(selected['tp1']))}，TP2={_fmt_px(float(selected['tp2']))}，"
            f"状态={'已触发' if selected.get('triggered') else '待触发'}\n"
        )
    else:
        lines.append("- **123入场**：当前未出现与背景方向一致的有效结构，维持观察。\n")
    lines.append("- **风险点**：事件驱动跳空、流动性变化、低成交量假突破会导致结构失效。\n")
    lines.append("- **免责声明**：仅作技术分析与程序化演示，不构成投资建议。\n")
    return "".join(lines) + "\n"


def format_brief_line(asset: dict[str, str], stats: dict[str, Any], research: dict[str, Any] | None = None) -> str:
    symbol = asset["symbol"]
    name = asset.get("name") or symbol
    extra = ""
    if research and isinstance(research, dict):
        items = research.get("items") or []
        total = research.get("total")
        if items:
            extra = f"，研报命中 {len(items)}"
            if isinstance(total, int):
                extra += f"/{total}"
    return (
        f"- {name}（{symbol}）: {stats.get('trend', 'N/A')}，"
        f"现价 {_fmt_px(float(stats['last']))}，Fib区 `{stats.get('price_vs_fib_zone', 'unknown')}`{extra}"
    )
