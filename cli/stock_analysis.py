#!/usr/bin/env python3
"""
市场报告 CLI：参数解析与调度入口。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.orchestrator import run as run_orchestrator


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="股票/加密货币 K 线分析：报告 + JSON")
    p.add_argument(
        "--provider",
        default="tickflow",
        help="数据源：tickflow（默认）/ gateio（加密货币）/ goldapi（贵金属）",
    )
    p.add_argument(
        "--config",
        default=str(_REPO_ROOT / "config" / "market_config.json"),
        help="市场配置文件路径",
    )
    p.add_argument("--market-brief", action="store_true", help="按配置 default_symbols 批量分析")
    p.add_argument("--symbol", default=None, help="单标的 symbol（如 AAPL / 600519.SH）")
    p.add_argument("--interval", default="1d", help="K线周期，默认 1d")
    p.add_argument("--limit", type=int, default=180, help="K线根数，默认 180")
    p.add_argument("--out-dir", default=str(_REPO_ROOT / "output"), help="输出根目录")
    p.add_argument("--report-only", action="store_true", help="兼容旧参数；当前默认仅输出报告")
    p.add_argument("--with-research", action="store_true", help="启用研报客（yanbaoke）搜索并写入 output/research/")
    p.add_argument("--research-n", type=int, default=3, help="研报搜索结果条数，默认 3（最大 500）")
    p.add_argument("--research-type", default="title", help="研报搜索类型：title 或 content，默认 title")
    p.add_argument(
        "--research-keyword",
        default=None,
        help="研报搜索关键词（可选；未指定则默认用标的名称）",
    )
    p.add_argument(
        "--mtf-interval",
        default="auto",
        help="多周期辅图：auto（默认按数据源自动）/ 如 4h 1wk 60m；配合 --no-mtf 关闭",
    )
    p.add_argument("--no-mtf", action="store_true", help="关闭多周期辅图拉取与共振字段")
    p.add_argument(
        "--analysis-style",
        choices=["auto", "stock", "crypto"],
        default="auto",
        help="分析引擎：auto（按 market/provider 自动）/stock/crypto",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    return run_orchestrator(args)


if __name__ == "__main__":
    raise SystemExit(main())
