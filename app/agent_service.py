from __future__ import annotations

from argparse import Namespace
import os
from pathlib import Path
from typing import Any

from app.guardrails import ensure_agent_response
from app.langgraph_flow import run_graph
from app.orchestrator import execute
from app.rag_index import RagIndex
from analysis.beijing_time import default_review_time_for_interval, review_time_has_explicit_clock
from analysis.kline_metrics import ma_snapshot_from_stats
from config.runtime_config import get_analysis_config
from tools.deepseek.client import DeepSeekError, generate_decision

REQUIRED_TEMPLATE_KEYS = (
    "综合倾向",
    "关键位(Fib)",
    "触发条件",
    "失效条件",
    "风险点",
    "下次复核时间",
)


class TaskRunner:
    def __init__(self, *, repo_root: Path | None = None) -> None:
        self.repo_root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
        self.default_config = self.repo_root / "config" / "market_config.json"
        self.default_out_dir = self.repo_root / "output"

    def _build_args(
        self,
        *,
        symbol: str,
        provider: str,
        interval: str,
        limit: int,
        out_dir: str | None,
        with_research: bool,
        research_n: int,
        research_type: str,
        research_keyword: str | None,
        mtf_interval: str,
        no_mtf: bool,
        analysis_style: str,
    ) -> Namespace:
        return Namespace(
            provider=provider,
            config=str(self.default_config),
            market_brief=False,
            symbol=symbol,
            interval=interval,
            limit=limit,
            out_dir=str(Path(out_dir).resolve()) if out_dir else str(self.default_out_dir),
            report_only=True,
            with_research=with_research,
            research_n=research_n,
            research_type=research_type,
            research_keyword=research_keyword,
            mtf_interval=mtf_interval,
            no_mtf=no_mtf,
            analysis_style=analysis_style,
        )

    def run_analysis(
        self,
        *,
        symbol: str,
        provider: str = "gateio",
        interval: str = "1d",
        limit: int = 180,
        out_dir: str | None = None,
        with_research: bool = False,
        research_n: int = 5,
        research_type: str = "title",
        research_keyword: str | None = None,
        mtf_interval: str = "auto",
        no_mtf: bool = False,
        analysis_style: str = "auto",
        question: str | None = None,
        use_rag: bool = True,
        rag_top_k: int = 5,
        use_llm_decision: bool = True,
    ) -> dict[str, Any]:
        args = self._build_args(
            symbol=symbol,
            provider=provider,
            interval=interval,
            limit=limit,
            out_dir=out_dir,
            with_research=with_research,
            research_n=research_n,
            research_type=research_type,
            research_keyword=research_keyword,
            mtf_interval=mtf_interval,
            no_mtf=no_mtf,
            analysis_style=analysis_style,
        )
        # 主链：LangGraph ReAct + ToolNode
        if use_llm_decision and _llm_enabled():
            try:
                payload = run_graph(
                    repo_root=self.repo_root,
                    symbol=symbol,
                    provider=provider,
                    interval=interval,
                    limit=limit,
                    out_dir=out_dir,
                    question=question,
                    rag_top_k=rag_top_k,
                    analysis_style=analysis_style,
                )
                return ensure_agent_response(payload, check_paths=False)
            except Exception as exc:
                # 兜底：图执行异常时回退原逻辑，确保服务可用性
                fallback = self._run_legacy_payload(
                    args=args,
                    symbol=symbol,
                    question=question,
                    use_rag=use_rag,
                    rag_top_k=rag_top_k,
                    use_llm_decision=True,
                )
                fallback.setdefault("meta", {})["langgraph_warning"] = str(exc)
                return ensure_agent_response(fallback, check_paths=False)

        payload = self._run_legacy_payload(
            args=args,
            symbol=symbol,
            question=question,
            use_rag=use_rag,
            rag_top_k=rag_top_k,
            use_llm_decision=use_llm_decision,
        )
        return ensure_agent_response(payload, check_paths=False)

    def _run_legacy_payload(
        self,
        *,
        args: Namespace,
        symbol: str,
        question: str | None,
        use_rag: bool,
        rag_top_k: int,
        use_llm_decision: bool,
    ) -> dict[str, Any]:
        result = execute(args, emit_logs=False)
        if int(result.get("exit_code", 1)) != 0:
            raise RuntimeError(f"任务执行失败: {result}")
        overview_items = result.get("overview_items")
        if not isinstance(overview_items, list) or not overview_items:
            raise RuntimeError("任务执行完成但无有效结果")
        symbol_u = symbol.strip().upper()
        chosen = None
        for it in overview_items:
            if isinstance(it, dict) and str(it.get("symbol") or "").upper() == symbol_u:
                chosen = it
                break
        if chosen is None:
            chosen = overview_items[0]
        if not isinstance(chosen, dict):
            raise RuntimeError("结果结构异常")
        return self._to_agent_payload(
            run_result=result,
            item=chosen,
            question=question,
            use_rag=use_rag,
            rag_top_k=rag_top_k,
            use_llm_decision=use_llm_decision,
        )

    def _to_agent_payload(
        self,
        *,
        run_result: dict[str, Any],
        item: dict[str, Any],
        question: str | None,
        use_rag: bool,
        rag_top_k: int,
        use_llm_decision: bool,
    ) -> dict[str, Any]:
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
        research = item.get("research") if isinstance(item.get("research"), dict) else {}
        for k in ("json_path", "md_path"):
            val = research.get(k)
            if isinstance(val, str) and val:
                evidence_sources.append({"source_path": val, "source_type": "research"})

        if use_rag and isinstance(question, str) and question.strip():
            root = Path(str(args_out_dir(run_result) or self.default_out_dir))
            rag_index = RagIndex.from_output_root(root, memory_paths=_memory_paths_from_config(self.repo_root))
            for hit in rag_index.query(question, top_k=max(1, int(rag_top_k))):
                source_path = str(hit.get("source_path") or "")
                source_type = str(hit.get("source_type") or "rag")
                if source_path and not any(x.get("source_path") == source_path for x in evidence_sources):
                    evidence_sources.append({"source_path": source_path, "source_type": source_type})

        entry = selected_setup.get("entry")
        stop = selected_setup.get("stop")
        triggered = selected_setup.get("triggered")
        analysis_result = {
            "symbol": item.get("symbol"),
            "name": item.get("name"),
            "provider": item.get("provider"),
            "interval": item.get("interval"),
            "trend": stats.get("trend"),
            "last_price": stats.get("last"),
            "fib_zone": stats.get("price_vs_fib_zone"),
            "regime_label": regime.get("label"),
            "regime_confidence": regime.get("confidence"),
            "trigger_conditions": {
                "entry": entry,
                "triggered": triggered,
                "stop": stop,
                "tp1": selected_setup.get("tp1"),
                "tp2": selected_setup.get("tp2"),
            },
            "invalidation_conditions": {
                "stop": stop,
                "time_stop_rule": ((stats.get("time_stop_v1") or {}).get("rule") if isinstance(stats.get("time_stop_v1"), dict) else None),
            },
            "risk_points": [
                "低流动性阶段容易出现假突破",
                "跨周期不共振时优先降仓或等待",
            ],
            "decision_source": "rules",
            "wyckoff_123_v1": wyckoff,
            "ma_snapshot": ma_snapshot_from_stats(stats),
        }
        fixed_template = _build_fixed_template(
            trend=str(stats.get("trend") or "未知"),
            fib_zone=str(stats.get("price_vs_fib_zone") or "未知"),
            trigger_data=analysis_result["trigger_conditions"],
            invalidation_data=analysis_result["invalidation_conditions"],
            risk_points=list(analysis_result["risk_points"]),
            interval=str(item.get("interval") or ""),
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

        llm_warning: str | None = None
        if use_llm_decision and _llm_enabled():
            try:
                llm_decision = generate_decision(
                    symbol=str(item.get("symbol") or ""),
                    interval=str(item.get("interval") or ""),
                    question=question,
                    technical_snapshot={
                        "trend": stats.get("trend"),
                        "last_price": stats.get("last"),
                        "fib_zone": stats.get("price_vs_fib_zone"),
                        "regime": regime.get("label"),
                        "trigger": {
                            "entry": entry,
                            "stop": stop,
                            "tp1": selected_setup.get("tp1"),
                            "tp2": selected_setup.get("tp2"),
                            "triggered": triggered,
                        },
                        "structure_flags": structure.get("flags"),
                        "ma_snapshot": ma_snapshot_from_stats(stats),
                    },
                    evidence_sources=evidence_sources,
                )
                analysis_result["llm_decision"] = llm_decision
                analysis_result["decision_source"] = "llm+rules"
                fixed_template = _normalize_fixed_template(llm_decision=llm_decision, fallback=fixed_template)
            except DeepSeekError as exc:
                llm_warning = str(exc)
        analysis_result["fixed_template"] = fixed_template

        payload: dict[str, Any] = {
            "analysis_result": analysis_result,
            "risk_flags": risk_flags,
            "evidence_sources": evidence_sources,
            "meta": {
                "session_dir": run_result.get("session_dir"),
                "symbols_processed": run_result.get("symbols_processed"),
                "journal": run_result.get("journal"),
            },
        }
        if llm_warning:
            payload["meta"]["llm_warning"] = llm_warning
        return payload


def args_out_dir(run_result: dict[str, Any]) -> str | None:
    session_dir = run_result.get("session_dir")
    if not isinstance(session_dir, str) or not session_dir:
        return None
    p = Path(session_dir)
    # session_dir: output/provider/market/day
    if len(p.parts) < 4:
        return str(p.parent)
    return str(p.parents[2])


def _llm_enabled() -> bool:
    enabled = os.getenv("AGENT_ENABLE_LLM", "1").strip().lower()
    if enabled in {"0", "false", "off", "no"}:
        return False
    if os.getenv("DEEPSEEK_API_KEY", "").strip():
        return True
    cfg = get_analysis_config()
    node = cfg.get("deepseek") if isinstance(cfg.get("deepseek"), dict) else {}
    return bool(str(node.get("api_key") or "").strip())


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


def _build_fixed_template(
    *,
    trend: str,
    fib_zone: str,
    trigger_data: dict[str, Any],
    invalidation_data: dict[str, Any],
    risk_points: list[str],
    interval: str,
) -> dict[str, Any]:
    trigger_text = (
        f"entry={trigger_data.get('entry')}，tp1={trigger_data.get('tp1')}，tp2={trigger_data.get('tp2')}，"
        f"triggered={trigger_data.get('triggered')}"
    )
    invalidation_text = (
        f"stop={invalidation_data.get('stop')}；time_stop_rule={invalidation_data.get('time_stop_rule')}"
    )
    review_time = default_review_time_for_interval(interval)
    return {
        "综合倾向": trend,
        "关键位(Fib)": fib_zone,
        "触发条件": trigger_text,
        "失效条件": invalidation_text,
        "风险点": risk_points if risk_points else ["常规波动风险"],
        "下次复核时间": review_time,
    }


def _normalize_fixed_template(*, llm_decision: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    out = dict(fallback)
    if not isinstance(llm_decision, dict):
        return out

    # 优先读取 LLM 明确给出的模板字段
    for k in REQUIRED_TEMPLATE_KEYS:
        if k in llm_decision and llm_decision[k] not in (None, ""):
            out[k] = llm_decision[k]

    # 兼容旧字段名
    if llm_decision.get("bias"):
        out["综合倾向"] = llm_decision["bias"]
    if llm_decision.get("trigger"):
        out["触发条件"] = llm_decision["trigger"]
    if llm_decision.get("invalidation"):
        out["失效条件"] = llm_decision["invalidation"]
    if llm_decision.get("review_time"):
        out["下次复核时间"] = llm_decision["review_time"]
    if llm_decision.get("risk_points"):
        out["风险点"] = llm_decision["risk_points"]

    # 兜底：确保风险点是数组
    if not isinstance(out.get("风险点"), list):
        out["风险点"] = [str(out.get("风险点") or "常规波动风险")]
    fb_rt = str(fallback.get("下次复核时间") or "")
    out_rt = str(out.get("下次复核时间") or "")
    if review_time_has_explicit_clock(fb_rt) and not review_time_has_explicit_clock(out_rt):
        out["下次复核时间"] = fallback["下次复核时间"]
    return out
