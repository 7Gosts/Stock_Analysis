from __future__ import annotations

import hashlib
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any

from analysis.beijing_time import beijing_calendar_day, to_beijing
from analysis import crypto_kline_analysis
from analysis import kline_metrics as stock_kline_metrics
from analysis import journal_policy
from analysis.kline_metrics import time_stop_deadline_utc
from analysis.price_feeds import fetch_ohlcv
from app import journal_service, report_writer
from intel.yanbaoke_client import write_research_bundle


def _normalize_asset_tags(raw: Any) -> list[str]:
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
    import json

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


def _to_local_iso(dt: datetime) -> str:
    return to_beijing(dt).replace(microsecond=0).isoformat()


def _output_market_bucket(assets_map: dict[str, dict[str, Any]], selected: list[str]) -> str:
    markets: list[str] = []
    for s in selected:
        it = assets_map.get(s) or {}
        mk = str(it.get("market") or "UNK").strip().upper() or "UNK"
        markets.append(mk)
    uniq = sorted(set(markets))
    if len(uniq) == 1:
        return uniq[0]
    return "MIXED"


def _safe_float(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _classify_order_kind_cn(signal_last: float, entry_zone: list[float]) -> str:
    lo = float(min(entry_zone))
    hi = float(max(entry_zone))
    return "实时单" if lo <= signal_last <= hi else "挂单"


def _compact_journal_for_notify(idea: dict[str, Any]) -> dict[str, Any]:
    """飞书/Agent meta 用摘要，避免塞入过大对象。"""
    reason = str(idea.get("strategy_reason") or "")
    if len(reason) > 220:
        reason = reason[:219] + "…"
    return {
        "idea_id": idea.get("idea_id"),
        "symbol": idea.get("symbol"),
        "interval": idea.get("interval"),
        "plan_type": idea.get("plan_type"),
        "direction": idea.get("direction"),
        "status": idea.get("status"),
        "entry_price": idea.get("entry_price"),
        "entry_zone": idea.get("entry_zone"),
        "stop_loss": idea.get("stop_loss"),
        "take_profit_levels": idea.get("take_profit_levels"),
        "rr": idea.get("rr"),
        "order_kind_cn": idea.get("order_kind_cn"),
        "strategy_reason": reason,
    }


def resolve_mtf_interval_effective(args: Namespace, market: str) -> tuple[str | None, str | None]:
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
            now_utc.isoformat(),
        ]
    )
    idea_id = hashlib.sha1(stable_key.encode("utf-8")).hexdigest()[:12]
    ts_v = stats.get("time_stop_v1") or {}
    bars = int(ts_v.get("max_wait_bars") or 5)
    ddl_iso = time_stop_deadline_utc(now_utc=now_utc, interval=interval, bars=bars)
    ddl_dt = datetime.fromisoformat(ddl_iso.replace("Z", "+00:00"))
    ddl_local_iso = _to_local_iso(ddl_dt)
    base_valid = _valid_until_utc(now_utc, interval)
    status = "filled" if triggered else ("pending" if aligned else "watch")
    valid_until = max(base_valid, ddl_dt) if status in {"pending", "watch"} else base_valid
    valid_until_local_iso = _to_local_iso(valid_until)
    now_local_iso = _to_local_iso(now_utc)
    mtf = stats.get("mtf_v1") or {}
    mtf_aligned: bool | None = mtf.get("aligned") if mtf.get("enabled") is True else None
    sf = stats.get("structure_filters_v1") or {}
    structure_flags = sf.get("flags") if isinstance(sf.get("flags"), list) else []
    wyckoff_bias = str(bg.get("bias") or "neutral")
    lifecycle_v1: dict[str, Any] = {
        "version": "v1",
        "time_stop_rule": str(ts_v.get("rule") or ""),
        "time_stop_deadline_utc": ddl_local_iso,
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
    out: dict[str, Any] = {
        "idea_id": idea_id,
        "created_at_utc": now_local_iso,
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
        "valid_until_utc": valid_until_local_iso,
        "status": status,
        "updated_at_utc": now_local_iso,
        "filled_at_utc": now_local_iso if triggered else None,
        "fill_price": round(fill_price, 6) if triggered else None,
        "exit_status": None,
        "closed_at_utc": None,
        "closed_price": None,
        "realized_pnl_pct": None,
        "risk_note": "仅技术分析演示，需结合风控独立决策。",
        "wyckoff_bias": wyckoff_bias,
        "wyckoff_aligned": aligned,
        "mtf_aligned": mtf_aligned,
        "structure_filter_flags": structure_flags,
        "time_stop_deadline_utc": ddl_local_iso,
        "lifecycle_v1": lifecycle_v1,
    }
    if tags:
        out["tags"] = tags
    return out


def execute(args: Namespace, *, emit_logs: bool = True) -> dict[str, Any]:
    def _log(message: str) -> None:
        if emit_logs:
            print(message, file=sys.stderr)

    if not args.market_brief and not args.symbol:
        _log("请指定 --market-brief 或 --symbol")
        return {"exit_code": 2, "error": "missing_target"}

    defaults, assets_map = load_market_config(Path(args.config).resolve())
    selected: list[str] = []
    if args.market_brief:
        selected = defaults
    if args.symbol:
        s = str(args.symbol).strip().upper()
        if s in assets_map:
            selected = [s]
        else:
            market = "CRYPTO" if "_" in s and s.endswith("USDT") else "US"
            assets_map[s] = {"symbol": s, "name": s, "market": market, "data_symbol": s}
            selected = [s]
    if not selected:
        _log("未找到可分析标的，请检查配置文件。")
        return {"exit_code": 2, "error": "empty_selection"}

    now_utc = datetime.now(timezone.utc)
    now_local = to_beijing(now_utc)
    out_base = Path(args.out_dir).resolve()
    provider_bucket = str(args.provider or "tickflow").strip().lower() or "tickflow"
    market_bucket = _output_market_bucket(assets_map, selected)
    day_bucket = beijing_calendar_day(now_utc)
    session_dir = out_base / provider_bucket / market_bucket / day_bucket
    session_dir.mkdir(parents=True, exist_ok=True)
    research_dir = out_base / "research" / provider_bucket / market_bucket / day_bucket

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
            _log(f"[跳过] {symbol} 拉取失败: {e}")
            continue
        if len(rows) < 30:
            _log(f"[跳过] {symbol} 数据不足（<30根）")
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
            _log(f"[跳过] {symbol} 指标计算失败")
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
                _log(f"[研报] {symbol} 搜索失败（已跳过）：{e}")
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
        swing_idea = journal_policy.maybe_build_swing_journal_entry(
            now_utc=now_utc,
            asset=asset,
            provider=str(args.provider),
            interval=args.interval,
            stats=stats,
            rows=rows,
        )
        if swing_idea:
            journal_candidates.append(swing_idea)

    journal_meta: dict[str, Any] | None = None
    if cards:
        report_path = session_dir / "full_report.md"
        brief_path = session_dir / "ai_brief.md"
        overview_path = session_dir / "ai_overview.json"
        full = (
            f"# 股票分析报告（北京时间 { report_writer.fmt_local_second(now_local) }）\n\n"
            + "".join(cards)
            + "## 免责声明\n\n"
            + "本文仅为技术分析与程序化演示，不构成任何投资建议。\n"
        )
        brief_text = (
            f"# 股票简报（北京时间 { report_writer.fmt_local_second(now_local) }）\n\n" + "\n".join(briefs) + "\n"
        )
        overview_payload = {
            "generated_at_utc": now_utc.isoformat(),
            "generated_at_local": report_writer.fmt_local_second(now_local),
            "generated_tz": str(now_local.tzinfo or "local"),
            "provider": args.provider,
            "interval": args.interval,
            "mtf_interval_effective": mtf_interval_effective,
            "items": overview,
        }
        report_writer.upsert_prepend_text(report_path, full)
        report_writer.upsert_prepend_text(brief_path, brief_text)
        report_writer.write_overview_latest(overview_path, overview_payload)
        report_writer.prune_legacy_timestamped_reports(session_dir)
        _log(f"[报告] {report_path}")
        _log(f"[简报] {brief_path}")
        _log(f"[总览] {overview_path}")

        journal_updated, journal_created, journal_path, stats_md, journal_new_entries = journal_service.process_journal(
            out_base=out_base,
            journal_candidates=journal_candidates,
            latest_rows_by_symbol=latest_rows_by_symbol,
            now_utc=now_utc,
        )
        if journal_created or journal_updated:
            _log(f"[台账] 更新 {journal_updated} 条，新增 {journal_created} 条 -> {journal_path}")
            if stats_md is not None:
                _log(f"[台账统计] {stats_md}")
                _log(f"[台账可读版] {journal_path.parent / 'trade_journal_readable.md'}")
        journal_meta = {
            "created": journal_created,
            "updated": journal_updated,
            "path": str(journal_path),
            "new_entries": [_compact_journal_for_notify(x) for x in journal_new_entries],
        }
    else:
        _log("无可写入报告的标的（可能均数据不足）。")

    _log(f"[会话目录] {session_dir}")
    out: dict[str, Any] = {
        "exit_code": 0,
        "provider": provider_bucket,
        "market": market_bucket,
        "session_dir": str(session_dir),
        "research_dir": str(research_dir),
        "symbols_requested": selected,
        "symbols_processed": [it.get("symbol") for it in overview],
        "overview_items": overview,
        "report_written": bool(cards),
        "report_path": str(session_dir / "full_report.md") if cards else None,
        "brief_path": str(session_dir / "ai_brief.md") if cards else None,
        "overview_path": str(session_dir / "ai_overview.json") if cards else None,
    }
    if journal_meta is not None:
        out["journal"] = journal_meta
    return out


def run(args: Namespace) -> int:
    result = execute(args, emit_logs=True)
    return int(result.get("exit_code", 1))

