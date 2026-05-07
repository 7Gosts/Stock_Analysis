from __future__ import annotations

from pathlib import Path
from typing import Any


REQUIRED_TOP_KEYS = {"analysis_result", "risk_flags", "evidence_sources"}
REQUIRED_TEMPLATE_KEYS = {"综合倾向", "关键位(Fib)", "触发条件", "失效条件", "风险点", "下次复核时间"}
ALLOWED_SOURCE_TYPES = {"kline", "research", "journal", "rag", "memory"}
FORBIDDEN_CLAIMS = (
    "已成交",
    "成交回报",
    "主力资金净流入",
    "交易所逐笔资金流",
)


def validate_agent_response(payload: dict[str, Any], *, check_paths: bool = False) -> list[str]:
    errors: list[str] = []
    missing = [k for k in REQUIRED_TOP_KEYS if k not in payload]
    if missing:
        errors.append(f"缺少顶层字段: {','.join(missing)}")
        return errors

    analysis_result = payload.get("analysis_result")
    if not isinstance(analysis_result, dict):
        errors.append("analysis_result 必须是对象")
    else:
        fixed_template = analysis_result.get("fixed_template")
        if not isinstance(fixed_template, dict):
            errors.append("analysis_result.fixed_template 必须是对象")
        else:
            missing_tpl = [k for k in REQUIRED_TEMPLATE_KEYS if k not in fixed_template]
            if missing_tpl:
                errors.append(f"fixed_template 缺少字段: {','.join(missing_tpl)}")
            risk_points = fixed_template.get("风险点")
            if not isinstance(risk_points, list) or not risk_points:
                errors.append("fixed_template.风险点 必须是非空数组")

    risk_flags = payload.get("risk_flags")
    if not isinstance(risk_flags, list):
        errors.append("risk_flags 必须是数组")

    evidence = payload.get("evidence_sources")
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence_sources 必须是非空数组")
    else:
        for idx, src in enumerate(evidence):
            if not isinstance(src, dict):
                errors.append(f"evidence_sources[{idx}] 必须是对象")
                continue
            st = str(src.get("source_type") or "")
            sp = str(src.get("source_path") or "")
            if st not in ALLOWED_SOURCE_TYPES:
                errors.append(f"evidence_sources[{idx}] source_type 不合法: {st}")
            if not sp:
                errors.append(f"evidence_sources[{idx}] 缺少 source_path")
            elif check_paths and not Path(sp).exists():
                errors.append(f"evidence_sources[{idx}] source_path 不存在: {sp}")

    # 对文本做基础安全检查，避免出现禁止口径。
    serialized = str(payload)
    for kw in FORBIDDEN_CLAIMS:
        if kw in serialized:
            errors.append(f"出现禁止口径: {kw}")
    return errors


def ensure_agent_response(payload: dict[str, Any], *, check_paths: bool = False) -> dict[str, Any]:
    errors = validate_agent_response(payload, check_paths=check_paths)
    if errors:
        raise ValueError(" ; ".join(errors))
    return payload
