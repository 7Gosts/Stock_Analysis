from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from analysis.beijing_time import format_beijing


def fmt_local_second(now_local: datetime) -> str:
    return format_beijing(now_local)


def upsert_prepend_text(path: Path, content: str, *, sep: str = "\n\n---\n\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        return
    old = path.read_text(encoding="utf-8")
    merged = content + (sep + old if old.strip() else "")
    path.write_text(merged, encoding="utf-8")


def _overview_item_key(it: Any) -> tuple[str, str, str]:
    if not isinstance(it, dict):
        return ("", "", "")
    sym = str(it.get("symbol") or "").strip().upper()
    iv = str(it.get("interval") or "").strip()
    pv = str(it.get("provider") or "").strip().lower()
    return (sym, iv, pv)


def _merge_overview_items(existing: list[Any], incoming: list[Any]) -> list[dict[str, Any]]:
    inc_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for it in incoming:
        if isinstance(it, dict):
            inc_by_key[_overview_item_key(it)] = it
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for it in existing:
        if not isinstance(it, dict):
            continue
        k = _overview_item_key(it)
        if k in inc_by_key:
            out.append(inc_by_key[k])
            seen.add(k)
        else:
            out.append(it)
    for it in incoming:
        if not isinstance(it, dict):
            continue
        k = _overview_item_key(it)
        if k not in seen:
            out.append(it)
            seen.add(k)
    return out


def write_overview_latest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dict(payload)
    body.pop("_history", None)
    items_in = body.get("items")
    if not isinstance(items_in, list):
        items_in = []
    body["items"] = items_in
    if path.is_file():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old = None
        if isinstance(old, dict):
            old_items = old.get("items")
            if isinstance(old_items, list) and old_items:
                body["items"] = _merge_overview_items(old_items, items_in)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


_LEGACY_REPORT_TS = re.compile(r"^(full_report|ai_brief|ai_overview)_\d{6,8}(\.json|\.md)$")


def prune_legacy_timestamped_reports(session_dir: Path) -> None:
    try:
        files = list(session_dir.iterdir())
    except OSError:
        return
    for p in files:
        if p.is_file() and _LEGACY_REPORT_TS.match(p.name):
            try:
                p.unlink()
            except OSError:
                pass

