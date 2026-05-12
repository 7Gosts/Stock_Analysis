#!/usr/bin/env python3
"""显式账户充提 / 调账（追加 account_ledger 快照）。

用法（在仓库根目录）:
  python scripts/account_cash_move.py deposit --currency USD --amount 1000 --note "入金"
  python scripts/account_cash_move.py withdraw --currency USD --amount 100
  python scripts/account_cash_move.py adjust --currency CNY --delta -50 --note "手续费"

需配置 database.postgres.dsn；账户须已有 journal_004 或等价 init 快照。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from loguru import logger

from persistence import account_service


def main() -> int:
    p = argparse.ArgumentParser(description="账户充提 / 调账（account_ledger）")
    sub = p.add_subparsers(dest="cmd", required=True)

    def _add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--currency", required=True, help="币种账户，如 USD / CNY")
        sp.add_argument("--note", default="", help="写入 meta.note（可选）")

    d = sub.add_parser("deposit", help="充值（增加 balance 与 available）")
    _add_common(d)
    d.add_argument("--amount", type=float, required=True, help="正数金额")

    w = sub.add_parser("withdraw", help="提现（减少 balance 与 available，可用不足则失败）")
    _add_common(w)
    w.add_argument("--amount", type=float, required=True, help="正数金额")

    a = sub.add_parser("adjust", help="调账（balance/available 同步增减 delta，可为负）")
    _add_common(a)
    a.add_argument("--delta", type=float, required=True, help="正数充值调、负数扣减")

    args = p.parse_args()
    note = str(args.note).strip() or None
    cur = str(args.currency).strip().upper()

    ok = False
    if args.cmd == "deposit":
        ok = account_service.deposit_funds(cur, float(args.amount), note=note)
    elif args.cmd == "withdraw":
        ok = account_service.withdraw_funds(cur, float(args.amount), note=note)
    else:
        ok = account_service.adjust_funds(cur, float(args.delta), note=note)

    if ok:
        logger.info("[cash_move] 完成 cmd={} currency={}", args.cmd, cur)
        return 0
    logger.error("[cash_move] 失败 cmd={} currency={}", args.cmd, cur)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
