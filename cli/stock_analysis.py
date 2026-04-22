#!/usr/bin/env python3
"""
市场报告 CLI：编排 `analysis`（拉数 + 指标 + 台账）与可选 `intel`（研报客）。

示例（在仓库根目录执行）:
  python cli/stock_analysis.py --market-brief
  python cli/stock_analysis.py --symbol AAPL --interval 1d --limit 180
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from analysis.kline_metrics import time_stop_deadline_utc
from analysis import kline_metrics as stock_kline_metrics
from analysis import crypto_kline_analysis
from analysis.price_feeds import fetch_ohlcv
from analysis.ledger_stats import write_latest_stats
from analysis.trade_journal import (
    has_active_idea,
    load_journal,
    save_journal,
    update_idea_with_rows,
)
from intel.yanbaoke_client import write_research_bundle


def _normalize_asset_tags(raw: Any) -> list[str]:
    """从配置项解析标签：支持字符串或字符串数组，去空、去首尾空白。"""
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    if isinstance(raw, list):
        out: list[str] = []
        for x in raw:
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    return []


def load_market_config(path: Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if not path.is_file():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    obj = json.loads(path.read_text(encoding="utf-8"))
    defaults_raw = obj.get("default_symbols") or []
    assets_raw = obj.get("assets") or []
    assets_map: dict[str, dict[str, Any]] = {}
    for it in assets_raw:
        if not isinstance(it, dict):
            continue
        symbol = str(it.get("symbol") or "").strip()
        ticker = str(it.get("data_symbol") or it.get("ak_symbol") or it.get("yf_ticker") or "").strip()
        if not symbol or not ticker:
            continue
        row: dict[str, Any] = {
            "symbol": symbol.upper(),
            "name": str(it.get("name") or symbol.upper()),
            "market": str(it.get("market") or "UNK").upper(),
            "data_symbol": ticker,
        }
        tags = _normalize_asset_tags(it.get("tags"))
        if tags:
            row["tags"] = tags
        assets_map[symbol.upper()] = row
    defaults: list[str] = []
    for x in defaults_raw:
        sx = str(x).strip().upper()
        if sx and sx in assets_map:
            defaults.append(sx)
    return defaults, assets_map


def _utc_day(now_utc: datetime) -> str:
    return now_utc.strftime("%Y-%m-%d")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _safe_float(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _classify_order_kind_cn(signal_last: float, entry_zone: list[float]) -> str:
    lo = float(min(entry_zone))
    hi = float(max(entry_zone))
    return "实时单" if lo <= signal_last <= hi else "挂单"


def resolve_mtf_interval_effective(args: argparse.Namespace, market: str) -> tuple[str | None, str | None]:
    """返回 (辅周期, 跳过原因)。auto：1d 主图按数据源选辅周期。"""
    if getattr(args, "no_mtf", False):
        return None, None
    raw = str(getattr(args, "mtf_interval", "auto") or "auto").strip()
    low = raw.lower()
    if low in {"none", "off", "0", "false"}:
        return None, None
    if low not in {"auto", ""}:
        return raw, None

    main = str(args.interval).strip().lower()
    prov = str(args.provider).strip().lower()
    mkt = str(market or "").upper()
    if main not in {"1d", "1day"}:
        return None, "auto_only_for_daily_main"

    if mkt == "CRYPTO" or prov == "gateio":
        return "4h", None
    if mkt in {"PM", "GOLD", "METAL"} or prov in {"goldapi", "gold_api", "gold-api"}:
        return "1w", None
    if prov == "tickflow":
        return "1w", None
    return "1w", None


def _valid_until_utc(now_utc: datetime, interval: str) -> datetime:
    iv = (interval or "").lower()
    if iv in {"1d", "1w", "1m", "1mo"}:
        return now_utc + timedelta(days=3)
    if iv in {"4h", "1h"}:
        return now_utc + timedelta(hours=24)
    return now_utc + timedelta(hours=12)


def build_trade_journal_entry(
    *,
    now_utc: datetime,
    asset: dict[str, Any],
    provider: str,
    interval: str,
    stats: dict[str, Any],
) -> dict[str, Any] | None:
    strategy = (stats.get("wyckoff_123_v1") or {}) if isinstance(stats, dict) else {}
    bg = strategy.get("background") or {}
    selected = strategy.get("selected_setup")
    setups = strategy.get("setups") or {}
    aligned = bool(strategy.get("aligned"))
    if not isinstance(selected, dict):
        if isinstance(setups.get("long"), dict):
            selected = setups["long"]
        elif isinstance(setups.get("short"), dict):
            selected = setups["short"]
    if not isinstance(selected, dict):
        return None

    entry = _safe_float(selected.get("entry"))
    stop = _safe_float(selected.get("stop"))
    tp1 = _safe_float(selected.get("tp1"))
    tp2 = _safe_float(selected.get("tp2"))
    last_px = _safe_float(stats.get("last"))
    if entry is None or stop is None or tp1 is None or tp2 is None:
        return None

    direction = "long" if str(selected.get("side") or "long") == "long" else "short"
    triggered = bool(selected.get("triggered"))
    zone_half = max(abs(entry) * 0.001, abs(entry - stop) * 0.2)
    entry_zone = [round(entry - zone_half, 6), round(entry + zone_half, 6)]
    fill_price = last_px if last_px is not None else entry
    order_kind_cn = _classify_order_kind_cn(fill_price, entry_zone)
    risk = abs(entry - stop)
    reward = abs(tp1 - entry)
    rr = round(reward / risk, 4) if risk > 1e-12 and reward > 1e-12 else None
    stable_key = "|".join(
        [
            str(asset.get("symbol") or "UNKNOWN"),
            str(interval or "1d"),
            "tactical",
            direction,
        ]
    )
    idea_id = hashlib.sha1(stable_key.encode("utf-8")).hexdigest()[:12]
    ts_v = stats.get("time_stop_v1") or {}
    bars = int(ts_v.get("max_wait_bars") or 5)
    ddl_iso = time_stop_deadline_utc(now_utc=now_utc, interval=interval, bars=bars)
    ddl_dt = datetime.fromisoformat(ddl_iso.replace("Z", "+00:00"))
    base_valid = _valid_until_utc(now_utc, interval)
    status = "filled" if triggered else ("pending" if aligned else "watch")
    valid_until = max(base_valid, ddl_dt) if status in {"pending", "watch"} else base_valid
    mtf = stats.get("mtf_v1") or {}
    mtf_aligned: bool | None = mtf.get("aligned") if mtf.get("enabled") is True else None
    sf = stats.get("structure_filters_v1") or {}
    structure_flags = sf.get("flags") if isinstance(sf.get("flags"), list) else []
    wyckoff_bias = str(bg.get("bias") or "neutral")
    lifecycle_v1: dict[str, Any] = {
        "version": "v1",
        "time_stop_rule": str(ts_v.get("rule") or ""),
        "time_stop_deadline_utc": ddl_iso,
        "invalidation_hints": [
            "价格有效突破/跌破结构止损位",
            "超过 time_stop_deadline_utc 仍未触发或结构被推翻，应重评",
            "结构过滤 flags 含 low_liquidity_volume / abnormally_narrow_range 时降权",
        ],
    }
    reason_tail = ""
    if mtf.get("enabled") is True:
        reason_tail += f"；MTF共振={'是' if mtf_aligned else '否'}"
    elif mtf.get("enabled") is False and mtf.get("reason"):
        reason_tail += f"；MTF不可用({mtf.get('reason')})"
    if structure_flags and structure_flags != ["normal"]:
        reason_tail += f"；结构过滤={','.join(str(x) for x in structure_flags[:4])}"
    tags = asset.get("tags")
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip() for t in tags if str(t).strip()]
    entry_out: dict[str, Any] = {
        "idea_id": idea_id,
        "created_at_utc": now_utc.isoformat(),
        "symbol": asset["symbol"],
        "asset": asset.get("name") or asset["symbol"],
        "market": asset.get("market") or "UNK",
        "provider": provider,
        "interval": interval,
        "plan_type": "tactical",
        "direction": direction,
        "entry_type": "market" if triggered else "limit",
        "order_kind_cn": order_kind_cn,
        "entry_zone": entry_zone,
        "entry_price": round(entry, 6),
        "signal_last": round(fill_price, 6),
        "position_risk_pct": 0.5,
        "stop_loss": round(stop, 6),
        "take_profit_levels": [round(tp1, 6), round(tp2, 6)],
        "rr": rr,
        "strategy_reason": (
            f"{interval} {stats.get('trend', 'N/A')}；"
            f"威科夫背景={bg.get('bias', 'neutral')}；"
            f"123状态={'已触发' if triggered else '待触发'}；"
            f"{'方向一致' if aligned else '方向观察'}"
            f"{reason_tail}"
        ),
        "valid_until_utc": valid_until.isoformat(),
        "status": status,
        "updated_at_utc": now_utc.isoformat(),
        "filled_at_utc": now_utc.isoformat() if triggered else None,
        "fill_price": round(fill_price, 6) if triggered else None,
        "exit_status": None,
        "closed_at_utc": None,
        "closed_price": None,
        "realized_pnl_pct": None,
        "risk_note": "仅技术分析演示，需结合风控独立决策。",
        "wyckoff_bias": wyckoff_bias,
        "mtf_aligned": mtf_aligned,
        "structure_filter_flags": structure_flags,
        "time_stop_deadline_utc": ddl_iso,
        "lifecycle_v1": lifecycle_v1,
    }
    if tags:
        entry_out["tags"] = tags
    return entry_out


def append_trade_journal(path: Path, entries: list[dict[str, Any]]) -> None:
    if not entries:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for item in entries:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description="股票/加密货币 K 线分析：报告 + JSON")
    p.add_argument(
        "--provider",
        default="tickflow",
        help="数据源：tickflow（默认）/ gateio（加密货币）/ goldapi（贵金属）",
    )
    p.add_argument(
        "--config",
        default=str(_REPO_ROOT / "config" / "market_config.json"),
        help="市场配置文件路径",
    )
    p.add_argument("--market-brief", action="store_true", help="按配置 default_symbols 批量分析")
    p.add_argument("--symbol", default=None, help="单标的 symbol（如 AAPL / 600519.SH）")
    p.add_argument("--interval", default="1d", help="K线周期，默认 1d")
    p.add_argument("--limit", type=int, default=180, help="K线根数，默认 180")
    p.add_argument("--out-dir", default=str(_REPO_ROOT / "output"), help="输出根目录")
    p.add_argument("--report-only", action="store_true", help="兼容旧参数；当前默认仅输出报告")
    p.add_argument("--with-research", action="store_true", help="启用研报客（yanbaoke）搜索并写入 output/research/")
    p.add_argument("--research-n", type=int, default=3, help="研报搜索结果条数，默认 3（最大 500）")
    p.add_argument("--research-type", default="title", help="研报搜索类型：title 或 content，默认 title")
    p.add_argument(
        "--research-keyword",
        default=None,
        help="研报搜索关键词（可选；未指定则默认用标的名称）",
    )
    p.add_argument(
        "--mtf-interval",
        default="auto",
        help="多周期辅图：auto（默认按数据源自动）/ 如 4h 1wk 60m；配合 --no-mtf 关闭",
    )
    p.add_argument("--no-mtf", action="store_true", help="关闭多周期辅图拉取与共振字段")
    p.add_argument(
        "--analysis-style",
        choices=["auto", "stock", "crypto"],
        default="auto",
        help="分析引擎：auto（按 market/provider 自动）/stock/crypto",
    )
    args = p.parse_args()

    if not args.market_brief and not args.symbol:
        print("请指定 --market-brief 或 --symbol", file=sys.stderr)
        return 2

    defaults, assets_map = load_market_config(Path(args.config).resolve())
    selected: list[str] = []
    if args.market_brief:
        selected = defaults
    if args.symbol:
        s = str(args.symbol).strip().upper()
        if s in assets_map:
            selected = [s]
        else:
            # 允许直接传原始代码，临时组装资产定义（默认美股；形如 BTC_USDT 走 CRYPTO）
            market = "CRYPTO" if "_" in s and s.endswith("USDT") else "US"
            assets_map[s] = {"symbol": s, "name": s, "market": market, "data_symbol": s}
            selected = [s]
    if not selected:
        print("未找到可分析标的，请检查配置文件。", file=sys.stderr)
        return 2

    now_utc = datetime.now(timezone.utc)
    out_base = Path(args.out_dir).resolve()
    session_dir = out_base / _utc_day(now_utc)
    session_dir.mkdir(parents=True, exist_ok=True)
    research_dir = out_base / "research" / _utc_day(now_utc)

    cards: list[str] = []
    briefs: list[str] = []
    overview: list[dict[str, Any]] = []
    journal_candidates: list[dict[str, Any]] = []
    latest_rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    mtf_interval_effective: str | None = None

    for symbol in selected:
        asset = assets_map[symbol]
        market = str(asset.get("market") or "").upper()
        mtf_interval_effective, mtf_skip_reason = resolve_mtf_interval_effective(args, market)
        if args.analysis_style == "stock":
            km = stock_kline_metrics
        elif args.analysis_style == "crypto":
            km = crypto_kline_analysis
        else:
            km = crypto_kline_analysis if (market == "CRYPTO" or str(args.provider).lower() == "gateio") else stock_kline_metrics
        try:
            rows = fetch_ohlcv(
                provider=args.provider,
                ticker=asset["data_symbol"],
                market=asset["market"],
                interval=args.interval,
                limit=args.limit,
            )
        except Exception as e:
            print(f"[跳过] {symbol} 拉取失败: {e}", file=sys.stderr)
            continue
        if len(rows) < 30:
            print(f"[跳过] {symbol} 数据不足（<30根）", file=sys.stderr)
            continue
        latest_rows_by_symbol[symbol] = rows

        mtf_rows: list[dict[str, Any]] | None = None
        mtf_note: str | None = None if mtf_interval_effective else mtf_skip_reason
        if mtf_interval_effective:
            try:
                mtf_rows = fetch_ohlcv(
                    provider=args.provider,
                    ticker=asset["data_symbol"],
                    market=asset["market"],
                    interval=mtf_interval_effective,
                    limit=min(int(args.limit), 400),
                )
            except Exception as e:
                mtf_rows = None
                mtf_note = f"fetch_failed:{e}"
            if not mtf_rows or len(mtf_rows) < 30:
                mtf_rows = None
                suf = "secondary_insufficient"
                mtf_note = f"{mtf_note};{suf}" if mtf_note else suf

        stats = km.compute_ohlc_stats(
            rows,
            interval=args.interval,
            secondary_rows=mtf_rows,
            secondary_interval=mtf_interval_effective if mtf_rows else None,
            market=market,
        )
        if not stats:
            print(f"[跳过] {symbol} 指标计算失败", file=sys.stderr)
            continue

        research: dict[str, Any] | None = None
        if args.with_research:
            kw = (args.research_keyword or asset["symbol"]).strip()
            try:
                research = write_research_bundle(
                    out_dir=research_dir,
                    keyword=kw,
                    n=args.research_n,
                    search_type=args.research_type,
                )
            except Exception as e:
                print(f"[研报] {symbol} 搜索失败（已跳过）：{e}", file=sys.stderr)
                research = None

        cards.append(km.format_report_card(asset, stats, research=research))
        briefs.append(km.format_brief_line(asset, stats, research=research))
        raw_tags = asset.get("tags")
        item_tags = raw_tags if isinstance(raw_tags, list) else []
        item_tags = [str(t).strip() for t in item_tags if str(t).strip()]
        overview.append(
            {
                "symbol": asset["symbol"],
                "name": asset["name"],
                "market": asset["market"],
                "tags": item_tags,
                "provider": args.provider,
                "interval": args.interval,
                "mtf_interval_requested": str(getattr(args, "mtf_interval", "") or ""),
                "mtf_interval_effective": mtf_interval_effective,
                "mtf_note": mtf_note,
                "stats": stats,
                "research": research,
            }
        )
        idea = build_trade_journal_entry(
            now_utc=now_utc,
            asset=asset,
            provider=args.provider,
            interval=args.interval,
            stats=stats,
        )
        if idea:
            journal_candidates.append(idea)

    if cards:
        full = (
            f"# 股票分析报告（UTC {now_utc.strftime('%Y-%m-%d %H:%M:%S')}）\n\n"
            + "".join(cards)
            + "## 免责声明\n\n"
            + "本文仅为技术分析与程序化演示，不构成任何投资建议。\n"
        )
        _write_text(session_dir / "full_report.md", full)
        _write_text(
            session_dir / "ai_brief.md",
            f"# 股票简报（UTC {now_utc.strftime('%Y-%m-%d %H:%M:%S')}）\n\n" + "\n".join(briefs) + "\n",
        )
        _write_text(
            session_dir / "ai_overview.json",
            json.dumps(
                {
                    "generated_at_utc": now_utc.isoformat(),
                    "provider": args.provider,
                    "interval": args.interval,
                    "mtf_interval_effective": mtf_interval_effective,
                    "items": overview,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        print(f"[报告] {session_dir / 'full_report.md'}", file=sys.stderr)
        print(f"[简报] {session_dir / 'ai_brief.md'}", file=sys.stderr)
        print(f"[总览] {session_dir / 'ai_overview.json'}", file=sys.stderr)

        journal_path = out_base / "trade_journal.jsonl"
        journal_entries = load_journal(journal_path)
        journal_updated = 0
        for e in journal_entries:
            sym = str(e.get("symbol") or "").upper()
            rows = latest_rows_by_symbol.get(sym)
            if rows and update_idea_with_rows(e, rows, now_utc):
                journal_updated += 1
        journal_created = 0
        for idea in journal_candidates:
            if has_active_idea(
                journal_entries,
                symbol=str(idea.get("symbol") or ""),
                interval=str(idea.get("interval") or ""),
                direction=str(idea.get("direction") or ""),
                plan_type=str(idea.get("plan_type") or "tactical"),
            ):
                continue
            journal_entries.append(idea)
            journal_created += 1
        if journal_created or journal_updated:
            save_journal(journal_path, journal_entries)
            print(
                f"[台账] 更新 {journal_updated} 条，新增 {journal_created} 条 -> {journal_path}",
                file=sys.stderr,
            )
            try:
                md_path, readable_path = write_latest_stats(journal_path)
                print(f"[台账统计] {md_path}", file=sys.stderr)
                print(f"[台账可读版] {readable_path}", file=sys.stderr)
            except Exception as e:
                print(f"[警告] 台账统计生成失败: {e}", file=sys.stderr)
    else:
        print("无可写入报告的标的（可能均数据不足）。", file=sys.stderr)

    print(f"[会话目录] {session_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
