#!/usr/bin/env python3
"""
仅调用研报客（yanbaoke）搜索并落盘，不拉行情。
用于「板块 / 概念 / 行业叙事」等关键词检索。

示例（仓库根目录）:
  python cli/yb_search.py --keyword "有色金属" --n 5
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from intel.yanbaoke_client import write_research_bundle


def main() -> int:
    p = argparse.ArgumentParser(description="研报客：仅关键词搜索并写入 output/research/")
    p.add_argument("--keyword", required=True, help="搜索关键词（如板块名、概念名、公司名）")
    p.add_argument("--n", type=int, default=5, help="条数，默认 5（最大 500）")
    p.add_argument("--type", default="title", choices=("title", "content"), help="搜索类型，默认 title")
    p.add_argument(
        "--out-dir",
        default=str(_REPO_ROOT / "output" / "research"),
        help="输出根目录，默认 output/research（下按本机日期分子目录）",
    )
    args = p.parse_args()
    now_utc = datetime.now(timezone.utc)
    day = now_utc.astimezone().strftime("%Y-%m-%d")
    out = Path(args.out_dir).resolve() / day
    try:
        r = write_research_bundle(
            out_dir=out,
            keyword=str(args.keyword).strip(),
            n=int(args.n),
            search_type=str(args.type).strip(),
        )
    except Exception as e:
        print(f"检索失败: {e}", file=sys.stderr)
        return 1
    print(f"[研报客] keyword={r.get('keyword')!r} total={r.get('total')!r} items={len(r.get('items') or [])}")
    print(f"[JSON] {r.get('json_path')}")
    print(f"[MD]   {r.get('md_path')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
