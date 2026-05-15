from __future__ import annotations

from typing import Any

from config.runtime_config import get_analysis_config
from tools.llm.client import LLMClientError, generate_feishu_narrative, generate_grounded_answer


def grounded_writer_enabled() -> bool:
    import os

    env = os.getenv("AGENT_ENABLE_GROUNDED_WRITER", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    cfg = get_analysis_config()
    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    if "enable_grounded_writer" in agent:
        return bool(agent.get("enable_grounded_writer"))
    fei = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    if fei.get("use_narrative_reply"):
        return True
    return True


def fallback_to_template_reply_enabled() -> bool:
    cfg = get_analysis_config()
    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    return bool(agent.get("fallback_to_template_reply", True))


def extract_narrative_facts_from_agent_payload(result_payload: dict[str, Any]) -> dict[str, Any]:
    """与飞书叙事旧逻辑一致：从 HTTP/LangGraph 结果抽取撰稿用事实。"""
    analysis = result_payload.get("analysis_result") if isinstance(result_payload.get("analysis_result"), dict) else {}
    meta = result_payload.get("meta") if isinstance(result_payload.get("meta"), dict) else {}
    out: dict[str, Any] = {}
    for key in (
        "symbol",
        "name",
        "provider",
        "interval",
        "trend",
        "last_price",
        "fib_zone",
        "regime_label",
        "regime_confidence",
        "decision_source",
    ):
        if key in analysis and analysis.get(key) is not None:
            out[key] = analysis.get(key)
    ft = analysis.get("fixed_template")
    if isinstance(ft, dict) and ft:
        out["fixed_template"] = ft
    ms = analysis.get("ma_snapshot")
    if isinstance(ms, dict) and ms:
        out["ma_snapshot"] = ms
    wy = analysis.get("wyckoff_123_v1")
    if isinstance(wy, dict):
        slim = {k: wy[k] for k in ("background", "preferred_side", "aligned", "selected_setup", "setups") if k in wy}
        if slim:
            out["wyckoff_123_v1"] = slim
    rp = meta.get("risk_profile")
    if isinstance(rp, str) and rp.strip():
        out["risk_profile"] = rp.strip()
    jn = meta.get("journal")
    if isinstance(jn, dict) and jn.get("new_entries"):
        out["journal_new_entries"] = jn.get("new_entries")
    return out


def write_grounded_reply(
    *,
    facts_bundle: dict[str, Any],
    user_question: str | None,
    task_type: str,
    response_mode: str,
    channel: str = "feishu",
    display_preferences: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Grounded writer：只引用 facts_bundle；失败抛 LLMClientError 由上层降级。"""
    _ = channel
    return generate_grounded_answer(
        facts_bundle=facts_bundle,
        user_question=user_question,
        task_type=task_type,
        response_mode=response_mode,
        display_preferences=display_preferences,
    )


def write_legacy_narrative_if_enabled(
    *,
    facts: dict[str, Any],
    user_question: str | None,
) -> str:
    """兼容旧接口：单标的 analysis facts → generate_feishu_narrative。"""
    return generate_feishu_narrative(facts=facts, user_question=user_question)


def safe_grounded_write(
    *,
    facts_bundle: dict[str, Any],
    user_question: str | None,
    task_type: str,
    response_mode: str,
    display_preferences: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not grounded_writer_enabled():
        return None
    try:
        return write_grounded_reply(
            facts_bundle=facts_bundle,
            user_question=user_question,
            task_type=task_type,
            response_mode=response_mode,
            display_preferences=display_preferences,
        )
    except (LLMClientError, Exception):
        return None
