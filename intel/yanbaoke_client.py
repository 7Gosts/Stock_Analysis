from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEARCH_SCRIPT = _REPO_ROOT / "tools" / "yanbaoke" / "scripts" / "search.mjs"
DEFAULT_DOWNLOAD_SCRIPT = _REPO_ROOT / "tools" / "yanbaoke" / "scripts" / "download.mjs"


def _slugify(text: str, *, max_len: int = 80) -> str:
    raw = text.strip()
    if not raw:
        return "query"
    # 允许中文/日文/韩文字符，避免关键词全是中文时退化成 query
    s = re.sub(r"[^\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af._-]+", "_", raw, flags=re.UNICODE)
    s = s.strip("_")
    if not s:
        s = "query"
    return s[:max_len]


def run_node_script(script_path: Path, args: list[str], *, timeout_sec: float = 60.0) -> str:
    node = shutil.which("node")
    if not node:
        raise RuntimeError("未找到 node，请先安装 Node.js（Ubuntu: sudo apt install -y nodejs npm）")
    cmd = [node, str(script_path), *args]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"node 脚本失败({proc.returncode}): {err}")
    return proc.stdout


def search_reports_markdown(
    keyword: str,
    *,
    n: int = 5,
    search_type: str = "title",
    script_path: Path | None = None,
    timeout_sec: float = 60.0,
) -> str:
    sp = script_path or DEFAULT_SEARCH_SCRIPT
    if not sp.is_file():
        raise FileNotFoundError(f"search.mjs 不存在: {sp}")
    args = [keyword, "-n", str(max(1, min(int(n), 500))), "--type", search_type]
    return run_node_script(sp, args, timeout_sec=timeout_sec)


def parse_search_markdown(md: str) -> dict[str, Any]:
    """
    将 search.mjs 的 Markdown 输出解析为结构化 JSON（尽量容错）。
    """
    lines = [ln.rstrip() for ln in (md or "").splitlines()]
    total = None
    items: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None

    for ln in lines:
        if ln.startswith("Total:"):
            m = re.search(r"Total:\s*(\d+)\s*reports", ln)
            if m:
                total = int(m.group(1))
            continue

        m_title = re.match(r"^- \*\*(.+)\*\*\s*$", ln)
        if m_title:
            if cur:
                items.append(cur)
            cur = {"title": m_title.group(1).strip()}
            continue

        if not cur:
            continue

        m_pub = re.match(r"^\s*Publisher:\s*(.+)\s*$", ln)
        if m_pub:
            cur["org_name"] = m_pub.group(1).strip()
            continue

        m_type = re.match(r"^\s*Type:\s*(.+)\s*$", ln)
        if m_type:
            cur["rtype_name"] = m_type.group(1).strip()
            continue

        m_pages = re.match(r"^\s*Pages:\s*(.+)\s*$", ln)
        if m_pages:
            try:
                cur["pagenum"] = int(m_pages.group(1).strip())
            except ValueError:
                cur["pagenum"] = m_pages.group(1).strip()
            continue

        m_date = re.match(r"^\s*Date:\s*(.+)\s*$", ln)
        if m_date:
            cur["time"] = m_date.group(1).strip()
            continue

        m_content = re.match(r"^\s*Content:\s*(.+)\s*$", ln)
        if m_content:
            cur["content"] = m_content.group(1).strip()
            continue

        m_uuid = re.match(r"^\s*UUID:\s*(.+)\s*$", ln)
        if m_uuid:
            cur["uuid"] = m_uuid.group(1).strip()
            continue

        m_url = re.match(r"^\s*(https?://\S+)\s*$", ln)
        if m_url:
            cur["url"] = m_url.group(1).strip()
            continue

    if cur:
        items.append(cur)

    return {"total": total, "items": items, "raw_md": md}


def search_reports_json(
    keyword: str,
    *,
    n: int = 5,
    search_type: str = "title",
    script_path: Path | None = None,
    timeout_sec: float = 60.0,
) -> dict[str, Any]:
    md = search_reports_markdown(keyword, n=n, search_type=search_type, script_path=script_path, timeout_sec=timeout_sec)
    return parse_search_markdown(md)


def download_report_markdown(
    uuid: str,
    *,
    api_key: str | None = None,
    fmt: str = "pdf",
    script_path: Path | None = None,
    timeout_sec: float = 60.0,
) -> str:
    dp = script_path or DEFAULT_DOWNLOAD_SCRIPT
    if not dp.is_file():
        raise FileNotFoundError(f"download.mjs 不存在: {dp}")
    args = [uuid]
    if api_key:
        args.append(api_key)
    args.append(f"--format={fmt}")
    return run_node_script(dp, args, timeout_sec=timeout_sec)


def write_research_bundle(
    *,
    out_dir: Path,
    keyword: str,
    n: int = 5,
    search_type: str = "title",
) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = search_reports_json(keyword, n=n, search_type=search_type)
    slug = _slugify(keyword)
    json_path = out_dir / f"{slug}_research.json"
    md_path = out_dir / f"{slug}_research.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(payload.get("raw_md") or "", encoding="utf-8")
    return {
        "keyword": keyword,
        "n": n,
        "search_type": search_type,
        "total": payload.get("total"),
        "items": payload.get("items") or [],
        "json_path": str(json_path),
        "md_path": str(md_path),
    }
