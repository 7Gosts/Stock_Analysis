#!/usr/bin/env python3
"""
真实调用 LLM「飞书意图路由」API（与线上 decide_feishu_route 使用同一套请求体）。

注：当前默认 provider 是 deepseek，但脚本命名与异常已 provider-agnostic。

需配置：环境变量 DEEPSEEK_API_KEY，或 config/analysis_defaults.yaml 中 deepseek.api_key。

用法（仓库根目录）：
  python3 scripts/feishu_route_llm_probe.py --text "你好"
  python3 scripts/feishu_route_llm_probe.py --text "看下 BTC 4h"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.feishu_asset_catalog import get_catalog_for_repo  # noqa: E402
from tools.llm.client import LLMClientError, feishu_route_deepseek_raw_and_routed  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description="真实调用 LLM 飞书路由（非单元测试 mock）。")
    p.add_argument("--text", default="你好", help="模拟用户发到飞书的文本")
    p.add_argument("--default-symbol", default="BTC_USDT", help="与飞书机器人 default_symbol 对齐")
    p.add_argument("--default-interval", default="4h", help="与飞书机器人 default_interval 对齐")
    p.add_argument("--timeout", type=float, default=30.0, help="请求超时秒数")
    args = p.parse_args()

    catalog = get_catalog_for_repo(_REPO_ROOT)
    assets = catalog.tradable_assets_for_prompt()

    try:
        raw, routed = feishu_route_deepseek_raw_and_routed(
            text=args.text,
            default_symbol=args.default_symbol,
            default_interval=args.default_interval,
            tradable_assets=assets,
            timeout_sec=args.timeout,
        )
    except LLMClientError as exc:
        print("LLMClientError:", exc, file=sys.stderr)
        return 1

    print("=== LLM chat/completions 原始响应（完整 JSON）===")
    print(json.dumps(raw, ensure_ascii=False, indent=2))
    print("\n=== 与本仓库 decide_feishu_route 相同的解析结果 ===")
    print(json.dumps(routed, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())