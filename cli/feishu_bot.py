#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.feishu_bot_service import run_feishu_bot


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="飞书机器人：WebSocket 收消息并调用本地分析 API 回复。")
    p.add_argument("--api-base-url", default="http://127.0.0.1:8000", help="分析 API 地址")
    return p


def main() -> int:
    args = build_parser().parse_args()
    run_feishu_bot(api_base_url=args.api_base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
