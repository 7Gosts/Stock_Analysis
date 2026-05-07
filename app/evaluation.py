from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agent_service import TaskRunner
from app.guardrails import REQUIRED_TEMPLATE_KEYS, validate_agent_response


@dataclass
class EvalCase:
    symbol: str
    provider: str = "gateio"
    interval: str = "4h"
    question: str = "按固定模板输出当前行情"
    use_llm_decision: bool = True


def load_eval_cases(path: Path) -> list[EvalCase]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"无法读取评估集: {path}") from exc
    if not isinstance(raw, list):
        raise RuntimeError("评估集格式错误：顶层必须为数组")

    cases: list[EvalCase] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        cases.append(
            EvalCase(
                symbol=symbol,
                provider=str(item.get("provider") or "gateio").strip(),
                interval=str(item.get("interval") or "4h").strip(),
                question=str(item.get("question") or "按固定模板输出当前行情").strip(),
                use_llm_decision=bool(item.get("use_llm_decision", True)),
            )
        )
    if not cases:
        raise RuntimeError("评估集为空")
    return cases


def evaluate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    errors = validate_agent_response(payload, check_paths=False)
    fixed_template = ((payload.get("analysis_result") or {}).get("fixed_template") if isinstance(payload.get("analysis_result"), dict) else {})
    tpl_ok = isinstance(fixed_template, dict) and all(k in fixed_template for k in REQUIRED_TEMPLATE_KEYS)
    evidence = payload.get("evidence_sources")
    evidence_ok = isinstance(evidence, list) and len(evidence) > 0
    hallucination_hit = any("禁止口径" in e for e in errors)
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "structure_ok": bool(tpl_ok),
        "factual_ok": bool(evidence_ok and (not hallucination_hit)),
        "hallucination_hit": hallucination_hit,
    }


def run_offline_eval(*, runner: TaskRunner, cases: list[EvalCase]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in cases:
        try:
            payload = runner.run_analysis(
                symbol=case.symbol,
                provider=case.provider,
                interval=case.interval,
                question=case.question,
                use_rag=True,
                rag_top_k=5,
                use_llm_decision=case.use_llm_decision,
            )
            scored = evaluate_payload(payload)
            rows.append(
                {
                    "case": case.__dict__,
                    "ok": scored["valid"],
                    "structure_ok": scored["structure_ok"],
                    "factual_ok": scored["factual_ok"],
                    "hallucination_hit": scored["hallucination_hit"],
                    "errors": scored["errors"],
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "case": case.__dict__,
                    "ok": False,
                    "structure_ok": False,
                    "factual_ok": False,
                    "hallucination_hit": False,
                    "errors": [str(exc)],
                }
            )
    total = max(1, len(rows))
    structure_rate = sum(1 for x in rows if x.get("structure_ok")) / total
    factual_rate = sum(1 for x in rows if x.get("factual_ok")) / total
    hallucination_rate = sum(1 for x in rows if x.get("hallucination_hit")) / total
    pass_rate = sum(1 for x in rows if x.get("ok")) / total
    return {
        "summary": {
            "total": len(rows),
            "pass_rate": round(pass_rate, 4),
            "structure_completeness_rate": round(structure_rate, 4),
            "factual_consistency_rate": round(factual_rate, 4),
            "hallucination_rate": round(hallucination_rate, 4),
        },
        "cases": rows,
    }
