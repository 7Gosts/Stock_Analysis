from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.agent_service import TaskRunner
from app.evaluation import load_eval_cases, run_offline_eval


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="运行 Agent 离线评估（结构完整性/事实一致性/幻觉率）")
    p.add_argument("--cases", default="tests/fixtures/agent_eval_cases.json", help="评估集 JSON 路径")
    p.add_argument("--out", default="output/eval_report.json", help="评估报告输出路径")
    return p


def main() -> int:
    args = build_parser().parse_args()
    cases_path = Path(args.cases).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve()
    runner = TaskRunner()
    cases = load_eval_cases(cases_path)
    report = run_offline_eval(runner=runner, cases=cases)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[eval] report={out_path}")
    print(f"[eval] summary={json.dumps(report.get('summary', {}), ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
