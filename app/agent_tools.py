from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from typing import Any

from app.orchestrator import execute
from app.rag_index import RagIndex
from config.runtime_config import get_analysis_config
from langchain_core.tools import tool


def _build_args(
    *,
    repo_root: Path,
    symbol: str,
    provider: str,
    interval: str,
    limit: int,
    out_dir: str | None,
    analysis_style: str,
) -> Namespace:
    return Namespace(
        provider=provider,
        config=str((repo_root / "config" / "market_config.json").resolve()),
        market_brief=False,
        symbol=symbol,
        interval=interval,
        limit=limit,
        out_dir=str(Path(out_dir).resolve()) if out_dir else str((repo_root / "output").resolve()),
        report_only=True,
        with_research=False,
        research_n=5,
        research_type="title",
        research_keyword=None,
        mtf_interval="auto",
        no_mtf=False,
        analysis_style=analysis_style,
    )


def _build_fixed_template(
    *,
    trend: str,
    fib_zone: str,
    trigger_data: dict[str, Any],
    invalidation_data: dict[str, Any],
    risk_points: list[str],
    interval: str,
) -> dict[str, Any]:
    review_time = "下个日线收盘后复核" if interval.lower() in {"1d", "1day"} else "下一根4hK线收盘后复核"
    return {
        "综合倾向": trend,
        "关键位(Fib)": fib_zone,
        "触发条件": (
            f"entry={trigger_data.get('entry')}，tp1={trigger_data.get('tp1')}，"
            f"tp2={trigger_data.get('tp2')}，triggered={trigger_data.get('triggered')}"
        ),
        "失效条件": (
            f"stop={invalidation_data.get('stop')}；"
            f"time_stop_rule={invalidation_data.get('time_stop_rule')}"
        ),
        "风险点": risk_points if risk_points else ["常规波动风险"],
        "下次复核时间": review_time,
    }


def _select_item(overview_items: list[Any], symbol: str) -> dict[str, Any] | None:
    symbol_u = symbol.strip().upper()
    for it in overview_items:
        if isinstance(it, dict) and str(it.get("symbol") or "").upper() == symbol_u:
            return it
    for it in overview_items:
        if isinstance(it, dict):
            return it
    return None


def _build_analysis_bundle(
    *,
    repo_root: Path,
    symbol: str,
    provider: str,
    interval: str,
    limit: int,
    out_dir: str | None,
    question: str | None,
    rag_top_k: int,
    analysis_style: str,
) -> dict[str, Any]:
    args = _build_args(
        repo_root=repo_root,
        symbol=symbol,
        provider=provider,
        interval=interval,
        limit=limit,
        out_dir=out_dir,
        analysis_style=analysis_style,
    )
    run_result = execute(args, emit_logs=False)
    if int(run_result.get("exit_code", 1)) != 0:
        raise RuntimeError(f"任务执行失败: {run_result}")
    overview_items = run_result.get("overview_items")
    if not isinstance(overview_items, list) or not overview_items:
        raise RuntimeError("任务执行完成但无有效结果")
    item = _select_item(overview_items, symbol=symbol)
    if not isinstance(item, dict):
        raise RuntimeError("结果结构异常")

    stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
    regime = stats.get("market_regime") if isinstance(stats.get("market_regime"), dict) else {}
    structure = stats.get("structure_filters_v1") if isinstance(stats.get("structure_filters_v1"), dict) else {}
    mtf = stats.get("mtf_v1") if isinstance(stats.get("mtf_v1"), dict) else {}
    wyckoff = stats.get("wyckoff_123_v1") if isinstance(stats.get("wyckoff_123_v1"), dict) else {}
    selected_setup = wyckoff.get("selected_setup") if isinstance(wyckoff.get("selected_setup"), dict) else {}

    evidence_sources: list[dict[str, Any]] = []
    for key, source_type in (
        ("overview_path", "kline"),
        ("report_path", "kline"),
        ("brief_path", "kline"),
    ):
        val = run_result.get(key)
        if isinstance(val, str) and val:
            evidence_sources.append({"source_path": val, "source_type": source_type})

    if isinstance(question, str) and question.strip():
        session_dir = run_result.get("session_dir")
        root = Path(str(session_dir)).parents[2] if isinstance(session_dir, str) and len(Path(session_dir).parts) >= 4 else (repo_root / "output")
        rag_index = RagIndex.from_output_root(root, memory_paths=_memory_paths_from_config(repo_root))
        for hit in rag_index.query(question, top_k=max(1, int(rag_top_k))):
            source_path = str(hit.get("source_path") or "")
            source_type = str(hit.get("source_type") or "rag")
            if source_path and not any(x.get("source_path") == source_path for x in evidence_sources):
                evidence_sources.append({"source_path": source_path, "source_type": source_type})

    trigger_conditions = {
        "entry": selected_setup.get("entry"),
        "triggered": selected_setup.get("triggered"),
        "stop": selected_setup.get("stop"),
        "tp1": selected_setup.get("tp1"),
        "tp2": selected_setup.get("tp2"),
    }
    invalidation_conditions = {
        "stop": selected_setup.get("stop"),
        "time_stop_rule": ((stats.get("time_stop_v1") or {}).get("rule") if isinstance(stats.get("time_stop_v1"), dict) else None),
    }
    risk_points = ["低流动性阶段容易出现假突破", "跨周期不共振时优先降仓或等待"]
    fixed_template = _build_fixed_template(
        trend=str(stats.get("trend") or "未知"),
        fib_zone=str(stats.get("price_vs_fib_zone") or "未知"),
        trigger_data=trigger_conditions,
        invalidation_data=invalidation_conditions,
        risk_points=risk_points,
        interval=str(interval),
    )

    risk_flags: list[str] = []
    flags = structure.get("flags")
    if isinstance(flags, list):
        for f in flags:
            sf = str(f).strip()
            if sf and sf != "normal":
                risk_flags.append(f"structure:{sf}")
    if mtf.get("enabled") is True and mtf.get("aligned") is False:
        risk_flags.append("mtf:unaligned")
    if str(regime.get("id") or "") == "transition":
        risk_flags.append("regime:transition")
    if not risk_flags:
        risk_flags.append("normal")

    return {
        "analysis_result": {
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "provider": item.get("provider"),
            "interval": item.get("interval"),
            "trend": stats.get("trend"),
            "last_price": stats.get("last"),
            "fib_zone": stats.get("price_vs_fib_zone"),
            "regime_label": regime.get("label"),
            "regime_confidence": regime.get("confidence"),
            "trigger_conditions": trigger_conditions,
            "invalidation_conditions": invalidation_conditions,
            "risk_points": risk_points,
            "decision_source": "rules",
            "fixed_template": fixed_template,
        },
        "risk_flags": risk_flags,
        "evidence_sources": evidence_sources,
        "meta": {
            "session_dir": run_result.get("session_dir"),
            "symbols_processed": run_result.get("symbols_processed"),
        },
    }


def make_tools(*, repo_root: Path) -> list[Any]:
    @tool
    def fetch_analysis_bundle(
        symbol: str,
        provider: str = "gateio",
        interval: str = "1d",
        limit: int = 180,
        out_dir: str | None = None,
        question: str | None = None,
        rag_top_k: int = 5,
        analysis_style: str = "auto",
    ) -> dict[str, Any]:
        """拉取行情并生成结构化分析快照（含固定模板、风险标记与证据源）。"""
        return _build_analysis_bundle(
            repo_root=repo_root,
            symbol=symbol,
            provider=provider,
            interval=interval,
            limit=limit,
            out_dir=out_dir,
            question=question,
            rag_top_k=rag_top_k,
            analysis_style=analysis_style,
        )

    return [fetch_analysis_bundle]


def _memory_paths_from_config(repo_root: Path) -> list[Path]:
    cfg = get_analysis_config()
    feishu = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    memory = feishu.get("memory") if isinstance(feishu.get("memory"), dict) else {}
    if not bool(memory.get("enabled", True)):
        return []
    backend = str(memory.get("backend") or "jsonl").strip().lower()
    if backend != "jsonl":
        return []
    raw = str(memory.get("memory_file") or "output/feishu_memory.jsonl").strip()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (repo_root / p).resolve()
    return [p] if p.exists() else []
