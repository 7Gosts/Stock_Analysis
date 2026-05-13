#!/usr/bin/env python3
"""对话型 Agent CLI：统一入口（三层重构 + 统一 Core 版）。

职责收敛为（adapter 层）：
1. 参数解析
2. 参数转换为统一 request schema
3. 调用统一 agent core
4. 渲染 reply_text 或输出 JSON

与现有 stock_analysis.py 的关系：
- stock_analysis.py 继续保留批处理和报告生成职责
- agent_run.py 是新增的对话型 / 智能体型 CLI 入口

文档参考：
- docs/AGENT_CORE_UNIFICATION_PLAN.md §6.2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.agent_schemas import AgentRequest, AgentResponse
from app.agent_core import handle_request


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="对话型 Agent CLI：统一入口（支持 chat/research/followup/analysis 等）"
    )
    p.add_argument(
        "text",
        nargs="?",
        default=None,
        help="用户输入文本（如：'看下半导体板块研报'、'BTC_USDT 日线怎么样'）",
    )
    p.add_argument(
        "--default-symbol",
        default="BTC_USDT",
        help="默认标的，如 BTC_USDT / AAPL",
    )
    p.add_argument(
        "--default-interval",
        default="4h",
        help="默认周期，如 4h / 1d",
    )
    p.add_argument(
        "--session-id",
        default=None,
        help="会话ID（可选，用于追问场景）",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式",
    )
    p.add_argument(
        "--interactive",
        action="store_true",
        help="交互模式（多轮对话）",
    )
    return p


def render_response(response: AgentResponse, *, json_output: bool = False) -> str:
    """渲染响应（CLI 显示）。"""
    if json_output:
        return json.dumps(response.to_dict(), ensure_ascii=False, indent=2)

    lines: list[str] = []

    # 任务类型标签
    task_label = {
        "chat": "【闲聊】",
        "clarify": "【澄清】",
        "quote": "【报价】",
        "compare": "【对比】",
        "analysis": "【分析】",
        "research": "【研报】",
        "followup": "【追问】",
    }.get(response.task_type, f"【{response.task_type}】")

    lines.append(task_label)

    # 回复内容
    reply = response.reply_text.strip()
    if reply:
        lines.append(reply)
    else:
        lines.append("（无回复内容）")

    # 分隔线
    lines.append("─" * 40)

    return "\n".join(lines)


def run_interactive_session(
    *,
    default_symbol: str,
    default_interval: str,
    json_output: bool,
) -> int:
    """交互模式：多轮对话。"""
    import uuid

    session_id = uuid.uuid4().hex[:8]
    print(f"启动交互对话（session_id: {session_id}）")
    print("输入 'exit' 或 'quit' 结束对话")
    print("─" * 40)

    while True:
        try:
            text = input("你: ").strip()
        except EOFError:
            print("\n对话结束")
            break
        except KeyboardInterrupt:
            print("\n对话结束")
            break

        if not text:
            continue
        if text.lower() in {"exit", "quit", "q", "bye"}:
            print("对话结束")
            break

        request = AgentRequest.from_cli(
            text=text,
            default_symbol=default_symbol,
            default_interval=default_interval,
            session_id=session_id,
        )

        response = handle_request(request)

        output = render_response(response, json_output=json_output)
        print(f"Agent: {output}")

    return 0


def main() -> int:
    args = build_parser().parse_args()

    # 交互模式
    if args.interactive:
        return run_interactive_session(
            default_symbol=args.default_symbol,
            default_interval=args.default_interval,
            json_output=args.json,
        )

    # 单次对话
    if args.text is None:
        print("错误：请提供输入文本，或使用 --interactive 进入交互模式")
        return 1

    request = AgentRequest.from_cli(
        text=args.text,
        default_symbol=args.default_symbol,
        default_interval=args.default_interval,
        session_id=args.session_id,
    )

    response = handle_request(request)
    output = render_response(response, json_output=args.json)
    print(output)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())