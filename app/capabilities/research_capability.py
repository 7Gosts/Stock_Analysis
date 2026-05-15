"""研报能力层：查看研报线索与叙事摘要。

通过 research_summary executor 产出 CapabilityResult，
与 market / sim_account 同级。
"""
from __future__ import annotations

from typing import Any

from app.query_engine.base import CapabilityResult
from app.executors.research_summary import run_research_summary


def view_research_digest(
    *,
    keyword: str,
    n: int = 5,
    search_type: str = "title",
) -> CapabilityResult:
    """查看研报检索摘要，返回 CapabilityResult。

    内部调用 run_research_summary，然后包装为统一的 CapabilityResult。
    """
    raw = run_research_summary(keyword=keyword, n=n, search_type=search_type)

    ok = raw.get("ok", False)
    kw = raw.get("keyword") or keyword
    items = raw.get("items") or []
    error = raw.get("error")
    source = raw.get("source") or "yanbaoke_search"

    if not ok:
        summary = f"研报检索暂不可用（关键词：{kw}）：{error or '未知错误'}。"
        return CapabilityResult(
            domain="research",
            intent="report",
            summary=summary,
            tables=[],
            metrics={"ok": False, "keyword": kw, "error": error},
            evidence_sources=[],
            meta={"source": source},
        )

    item_titles = [str(it.get("title") or "")[:60] for it in items if isinstance(it, dict)]
    summary = f"研报线索（关键词：{kw}，共 {len(items)} 条）：\n" + "\n".join(f" · {t}" for t in item_titles[:10])
    if len(item_titles) > 10:
        summary += f"\n · ...共 {len(item_titles)} 条"

    return CapabilityResult(
        domain="research",
        intent="report",
        summary=summary,
        tables=items,
        metrics={
            "ok": True,
            "keyword": kw,
            "total": raw.get("total"),
            "item_count": len(items),
        },
        evidence_sources=[f"{source}:search:{kw}"],
        meta={"source": source, "keyword": kw},
    )