from __future__ import annotations

from typing import Any

from .kline_metrics import (
    compute_ohlc_stats as _compute_stock_stats,
    format_brief_line as _format_stock_brief,
    format_report_card as _format_stock_card,
)


def _market_regime(stats: dict[str, Any], last: float, sma21: float | None, sma55: float | None) -> dict[str, Any]:
    hh = float(stats.get("swing_high") or last)
    ll = float(stats.get("swing_low") or last)
    range_pct = abs(hh - ll) / max(abs(last), 1e-12) * 100.0
    p21 = float(stats.get("p_ma_mid_pct") or 0.0)
    p55 = float(stats.get("p_ma_long_pct") or 0.0)
    trend_strength = abs(p21) + abs(p55)
    regime_id = "transition"
    regime_cn = "过渡震荡"
    conf = 52
    if trend_strength < 0.28 and range_pct < 1.2:
        regime_id, regime_cn, conf = "range", "窄幅震荡", 74
    elif range_pct >= 2.0 and trend_strength < 0.9:
        regime_id, regime_cn, conf = "high_vol_chop", "高波动震荡", 68
    elif sma21 and sma55 and last > sma21 > sma55:
        regime_id, regime_cn, conf = "trend_up", "趋势上行", 78
    elif sma21 and sma55 and last < sma21 < sma55:
        regime_id, regime_cn, conf = "trend_down", "趋势下行", 78
    return {
        "id": regime_id,
        "label": regime_cn,
        "confidence": conf,
        "range_pct_swing": round(range_pct, 3),
    }


def compute_ohlc_stats(
    rows: list[dict[str, Any]],
    *,
    interval: str,
    secondary_rows: list[dict[str, Any]] | None = None,
    secondary_interval: str | None = None,
    market: str | None = None,
) -> dict[str, Any] | None:
    """
    复用现有股票指标主干，并叠加 CryptoTradeDesk 的 MA 体系（8/21/55）与 regime 字段。
    """
    base = _compute_stock_stats(
        rows,
        interval=interval,
        secondary_rows=secondary_rows,
        secondary_interval=secondary_interval,
        market=market,
    )
    if not base:
        return None
    last = float(base["last"])
    ma = base.get("ma_system") or {}
    ma21 = float(ma.get("sma_mid")) if isinstance(ma, dict) and isinstance(ma.get("sma_mid"), (int, float)) else None
    ma55 = float(ma.get("sma_long")) if isinstance(ma, dict) and isinstance(ma.get("sma_long"), (int, float)) else None
    base["market_regime"] = _market_regime(base, last, ma21, ma55)
    return base


def format_report_card(asset: dict[str, Any], stats: dict[str, Any], research: dict[str, Any] | None = None) -> str:
    text = _format_stock_card(asset, stats, research=research)
    regime = stats.get("market_regime") or {}
    extra = f"- **市场状态（Regime）**：{regime.get('label', '—')}（confidence={regime.get('confidence', '—')}）\n"
    return text.replace("- **免责声明**：仅作技术分析与程序化演示，不构成投资建议。\n", extra + "- **免责声明**：仅作技术分析与程序化演示，不构成投资建议。\n")


def format_brief_line(asset: dict[str, Any], stats: dict[str, Any], research: dict[str, Any] | None = None) -> str:
    line = _format_stock_brief(asset, stats, research=research)
    regime = stats.get("market_regime") or {}
    label = str(regime.get("label") or "").strip()
    return f"{line}，Regime={label}" if label else line

