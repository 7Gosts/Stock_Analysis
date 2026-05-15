"""研报 / research 路径：RAG 命中或 ``run_research_summary`` + merge_facts_bundle。"""
from __future__ import annotations

from typing import Any

from app.executors.facts_bundle import merge_facts_bundle
from app.executors.research_summary import run_research_summary


def build_research_facts_bundle(
    *,
    rag_index: Any,
    user_question: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """构建 research 用 facts_bundle；返回 ``(facts_bundle, research_keyword)``。"""
    sym = str(payload.get("symbol") or "").strip()
    kw = str(payload.get("research_keyword") or sym or user_question or "").strip()

    hits = rag_index.query(kw, top_k=5, source_type_filter="research")
    if hits:
        rs_facts: dict[str, Any] = {"ok": True, "keyword": kw, "items": []}
        for hit in hits:
            snippet = str(hit.get("snippet") or "")
            title = snippet.split("title=")[-1].split(" org=")[0] if "title=" in snippet else snippet[:50]
            rs_facts["items"].append({
                "title": title,
                "source_path": hit.get("source_path"),
                "score": hit.get("score"),
            })
    else:
        rs_facts = run_research_summary(keyword=kw, n=5)

    fb = merge_facts_bundle(
        task_type="research",
        response_mode="narrative",
        user_question=user_question,
        symbols=[sym] if sym else [],
        research_facts=rs_facts,
        evidence_sources=[{"source_path": "yanbaoke:search", "source_type": "research"}],
        risk_flags=["normal"] if rs_facts.get("ok") else ["research:degraded"],
        trace={"executors": ["research_summary"], "keyword": kw},
    )
    return fb, kw
