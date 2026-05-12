from __future__ import annotations

from typing import Any


def run_research_summary(
    *,
    keyword: str,
    n: int = 5,
    search_type: str = "title",
) -> dict[str, Any]:
    """研报检索摘要事实（需本机 Node）；不替代 K 线触发位。"""
    kw = (keyword or "").strip()
    if not kw:
        return {"ok": False, "error": "empty_keyword", "items": [], "keyword": ""}
    try:
        from intel.yanbaoke_client import parse_search_markdown, search_reports_markdown

        md = search_reports_markdown(kw, n=max(1, min(int(n), 20)), search_type=search_type)
        parsed = parse_search_markdown(md)
        items = parsed.get("items") if isinstance(parsed.get("items"), list) else []
        slim: list[dict[str, Any]] = []
        for it in items[:20]:
            if not isinstance(it, dict):
                continue
            slim.append(
                {
                    "title": it.get("title"),
                    "org_name": it.get("org_name"),
                    "time": it.get("time"),
                    "content": (str(it.get("content") or "")[:400] + "…") if len(str(it.get("content") or "")) > 400 else it.get("content"),
                }
            )
        return {
            "ok": True,
            "keyword": kw,
            "items": slim,
            "total": parsed.get("total"),
            "source": "yanbaoke_search",
        }
    except Exception as exc:
        return {"ok": False, "keyword": kw, "error": str(exc), "items": [], "source": "yanbaoke_search"}
