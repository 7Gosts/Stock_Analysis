# 命名空间与文件速查

协作时建议：**先看本页**，再打开具体实现。Python 包名与磁盘目录一致（仓库根须在 `sys.path` 中，由 `cli/stock_analysis.py` 注入）。

---

## 顶层包（目录 = 包名）

| 目录 | 包名 | 一句话 |
|------|------|--------|
| `analysis/` | `analysis` | 拉行情、算指标、写报告片段、台账统计、贵金属 Gold API |
| `intel/` | `intel` | 研报客：搜索/解析/落盘（调 `tools/yanbaoke`） |
| `market_data/` | `market_data` | **预留**：板块/概念/资金流等结构化数据（当前无业务代码） |
| `cli/` | `cli` | 命令行编排（非业务逻辑堆积处） |

与 **`tools/yanbaoke/`** 区分：`intel` 是 **Python 封装**；`tools/yanbaoke` 是 **Node 脚本 + SKILL**。

---

## `analysis/` 内文件

| 文件 | 职责 |
|------|------|
| `price_feeds.py` | 统一入口 `fetch_ohlcv(provider, …)`：tickflow / akshare / alltick / goldapi |
| `kline_metrics.py` | SMA/Fib/趋势、威科夫背景、123 结构、`structure_filters_v1`、`time_stop_v1`、`mtf_v1`（辅周期可选）、`compute_ohlc_stats`、`format_*` 报告片段 |
| `ledger_stats.py` | 读 `trade_journal.jsonl`，生成周/月统计、`breakdown_*d`（按 status / wyckoff_bias、时间止损过期挂单数）、可读 Markdown |
| `gold_api.py` | Gold API（gold-api.cn）HTTP：品种解析、`fetch_ohlcv_goldapi` |

---

## `intel/` 内文件

| 文件 | 职责 |
|------|------|
| `yanbaoke_client.py` | `search_reports_*`、`write_research_bundle`（`subprocess` 调 `search.mjs`） |

---

## `cli/` 内文件

| 文件 | 职责 |
|------|------|
| `stock_analysis.py` | 唯一主 CLI：读 `config/market_config.json`、循环标的、可选辅周期 K 线（`--mtf-interval` / `--no-mtf`）、调 `analysis` + 可选 `intel`、写 `output/` |

---

## 配置与工具

| 路径 | 职责 |
|------|------|
| `config/market_config.json` | 标的列表、`default_symbols`、`market` / `data_symbol` |
| `tools/yanbaoke/scripts/search.mjs` | 研报客搜索（无 Key 可搜） |
| `tools/yanbaoke/scripts/download.mjs` | 研报下载（需 Key） |
| `tools/yanbaoke/SKILL.md` | 研报客技能说明 |

---

## 产物目录（非包）

| 路径 | 内容 |
|------|------|
| `output/<UTC日期>/` | `ai_brief.md`、`ai_overview.json`、`full_report.md` |
| `output/research/<UTC日期>/` | 研报搜索落盘的 `*_research.json` / `*.md`（目录名仍为 `research`，表示「研报产物」） |
| `output/trade_journal*.jsonl/md/json` | 台账与统计快照 |

---

## 常见 import（给写代码的人）

```python
from analysis.price_feeds import fetch_ohlcv
from analysis.kline_metrics import compute_ohlc_stats, format_report_card, format_brief_line
from analysis.ledger_stats import write_latest_stats
from intel.yanbaoke_client import write_research_bundle
```

---

## 历史重命名（便于搜旧讨论）

| 曾用名 | 现用名 |
|--------|--------|
| `technical/` | `analysis/` |
| `research/`（Python 包） | `intel/` |
| `analysis_engine.py` | `kline_metrics.py` |
| `data_providers.py` | `price_feeds.py` |
| `trade_journal_stats.py` | `ledger_stats.py` |
