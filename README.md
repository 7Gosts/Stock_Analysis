# Stock_Analysis

面向 A 股/美股的第一版 K 线分析工具，风格与 `CryptoTradeDesk` 一致：拉取行情、计算结构指标、生成报告与 JSON。

## 1. 当前能力（v1）

- 数据源：`tickflow`（默认）/ `akshare` / `alltick`
- 市场：A 股 + 美股（`tickflow` / `alltick` 也支持港股）
- 输出：
  - `output/<UTC日期>/full_report.md`
  - `output/<UTC日期>/ai_brief.md`
  - `output/<UTC日期>/ai_overview.json`
  - `output/trade_journal.jsonl`（策略候选台账，按次追加）
  - `output/trade_journal_stats_latest.md` / `output/trade_journal_stats_latest.json`（周/月统计快照）
  - `output/trade_journal_readable.md`（台账可读版，便于人工复盘）
- 指标（轻量）：
  - SMA20 / SMA60
  - 近 1 根、近 5 根涨跌幅
  - 最近窗口 swing high/low
  - Fib 关键位与现价所在区间
  - 趋势标签（偏多 / 偏空 / 震荡）
- 交易辅助（v1）：
  - 威科夫量价背景过滤（bias: `long_only` / `short_only` / `neutral`）
  - 123 结构识别（P1/P2/P3 + 触发价/止损/TP1/TP2）
  - 仅作技术分析辅助，不构成投资建议

## 2. 安装依赖

```bash
pip install -r Stock_Analysis/requirements.txt
```

如需启用研报客（yanbaoke）搜索，请额外安装 Node.js 18+：

```bash
sudo apt update
sudo apt install -y nodejs npm
node -v
```

## 3. 快速开始

```bash
# 按配置跑多标的简报
python3 Stock_Analysis/stock_analysis.py --market-brief --out-dir Stock_Analysis/output

# 单标的（symbol 可写配置里的 symbol 或原始代码）
python3 Stock_Analysis/stock_analysis.py --symbol AAPL --interval 1d --limit 180 --out-dir Stock_Analysis/output

# 默认即 TickFlow（无 API Key 时自动走免费日线服务）
python3 Stock_Analysis/stock_analysis.py --market-brief --report-only --out-dir Stock_Analysis/output

# 显式指定 TickFlow（等价于默认行为）
python3 Stock_Analysis/stock_analysis.py --provider tickflow --market-brief --report-only --out-dir Stock_Analysis/output

# 指定 AllTick（需要先设置 ALLTICK_TOKEN）
ALLTICK_TOKEN="你的token" python3 Stock_Analysis/stock_analysis.py --provider alltick --market-brief --report-only --out-dir Stock_Analysis/output

# 仅报告（兼容旧参数，当前默认就是仅报告）
python3 Stock_Analysis/stock_analysis.py --market-brief --report-only --out-dir Stock_Analysis/output

# 统计历史开单策略（周/月）
python3 Stock_Analysis/trade_journal_stats.py --journal Stock_Analysis/output/trade_journal.jsonl

# 附带研报搜索（写入 output/research/<UTC日期>/）
python3 Stock_Analysis/stock_analysis.py --market-brief --report-only --out-dir Stock_Analysis/output --with-research --research-n 3
```

## 4. 配置文件

默认读取 `Stock_Analysis/market_config.json`，结构示例：

```json
{
  "default_symbols": ["600519.SH", "AAPL"],
  "assets": [
    {
      "symbol": "600519.SH",
      "name": "贵州茅台",
      "market": "CN",
      "data_symbol": "600519.SH"
    }
  ]
}
```

字段说明：

- `symbol`: 你在命令行里使用的标识
- `name`: 展示名称
- `market`: `CN` / `US`
- `data_symbol`: 实际拉数代码（A股如 `600519.SH`，美股如 `AAPL`）

## 5. 说明

- provider 说明：
  - `tickflow`：默认 provider；支持免费日线（无 `TICKFLOW_API_KEY` 时自动走 `free-api.tickflow.org`）；
  - `akshare`：无需 token，国内可用性较好；
  - `alltick`：需要环境变量 `ALLTICK_TOKEN`。
- 如需 TickFlow 完整服务（分钟线/实时），设置：`export TICKFLOW_API_KEY="你的key"`。
- 对话提示词：`Stock_Analysis/AI_股票对话提示.md`（含“新增股票先改 market_config”规则）。
- 本工具为技术分析辅助，不构成投资建议。
- 台账说明：
  - 每次运行若识别到 123 候选结构，会向 `output/trade_journal.jsonl` 追加候选策略；
  - `idea_id` 使用稳定键（标的+周期+plan_type+方向）生成，便于跟踪同一策略的状态变化；
  - `status=filled` 表示本次快照已触发；`status=pending` 表示方向一致待触发；`status=watch` 表示仅观察；
  - 脚本会自动刷新 `trade_journal_stats_latest.md/json` 与 `trade_journal_readable.md`；
  - `trade_journal_readable.md` 默认按 `idea_id` 仅保留“最新状态”一行，避免历史重复刷屏。
- 研报客（yanbaoke）说明：
  - 脚本位于 `Stock_Analysis/tools/yanbaoke/`（不依赖 OpenClaw）；
  - Python 封装见 `Stock_Analysis/yanbaoke_client.py`（`subprocess` 调用 `node search.mjs`）；
  - 搜索免费；下载需要 `YANBAOKE_API_KEY`（见 `tools/yanbaoke/SKILL.md`）。

## 6. v1 交易辅助解读（威科夫 + 123）

报告中新增的 `威科夫背景过滤（v1）` 与 `123入场` 字段，解读方式如下：

- 背景过滤：
  - `long_only`：只关注多头 123 结构；
  - `short_only`：只关注空头 123 结构；
  - `neutral`：不强行给方向，等待更清晰量价共振。
- 123 结构：
  - 多头：低点 P1 -> 反弹高点 P2 -> 回踩低点 P3（且 P3 高于 P1），突破 P2 为触发；
  - 空头：高点 P1 -> 回落低点 P2 -> 反抽高点 P3（且 P3 低于 P1），跌破 P2 为触发。
- 计划参数：
  - `entry` 触发价；
  - `stop` 结构失效位（含微小缓冲）；
  - `tp1/tp2` 分别按约 1.5R / 2.5R 给出。

