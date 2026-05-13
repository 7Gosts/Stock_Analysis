from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.guardrails import ensure_agent_response
from app.langgraph_flow import run_graph
from config.runtime_config import get_llm_runtime_settings


class TaskRunner:
    """Agent 分析入口：仅 LangGraph（DeepSeek + 工具）；不再提供 execute + 规则模板兜底。"""

    def __init__(self, *, repo_root: Path | None = None) -> None:
        self.repo_root = (repo_root or Path(__file__).resolve().parents[1]).resolve()
        self.default_out_dir = self.repo_root / "output"

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
        # 与 FastAPI / eval 请求体字段对齐；当前 LangGraph 内 fetch_analysis_bundle 仍固定部分 CLI 参数
        _ = (with_research, research_n, research_type, research_keyword, mtf_interval, no_mtf, use_rag)
        if not use_llm_decision:
            raise RuntimeError("分析已强制为仅 LLM（LangGraph）流程：use_llm_decision 必须为 True。")
        if not _llm_enabled():
            raise RuntimeError(
                "分析依赖 LLM：请配置环境变量 LLM_API_KEY / <PROVIDER>_API_KEY，或 YAML 中 llm.providers.<provider>.api_key，"
                "且勿将 AGENT_ENABLE_LLM 设为 0。"
            )
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


def _llm_enabled() -> bool:
    enabled = os.getenv("AGENT_ENABLE_LLM", "1").strip().lower()
    if enabled in {"0", "false", "off", "no"}:
        return False
    settings = get_llm_runtime_settings()
    return bool(str(settings.get("api_key") or "").strip())
