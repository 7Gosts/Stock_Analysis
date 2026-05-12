# 命名空间与文件速查

协作时建议：**新对话 / 新 Agent 窗口**先看仓库根目录 **`AGENTS.md`**（角色边界与 **§0.2 路由**：叙事 vs K 线数据源），再翻本速查表与具体实现。Python 包名与磁盘目录一致（仓库根须在 `sys.path` 中，由 `cli/stock_analysis.py` 注入）。

---

## 顶层包（目录 = 包名）

| 目录 | 包名 | 一句话 |
|------|------|--------|
| `analysis/` | `analysis` | 业务分析层：provider 分发与归一化、指标计算、报告片段、台账统计 |
| `app/` | `app` | 应用编排层：流程调度、报告写入、台账服务 |
| `intel/` | `intel` | 研报客：搜索/解析/落盘（调 `tools/yanbaoke`） |
| `market_data/` | `market_data` | **预留**：板块/概念/资金流等结构化数据（当前无业务代码） |
| `cli/` | `cli` | 命令行编排（非业务逻辑堆积处） |
| `persistence/` | `persistence` | PostgreSQL：连接池、SQL 文件加载、台账仓库、账户与纸交易写入 |
| `config/` | `config` | 配置层：`market_config.json`、`analysis_defaults.yaml`、`runtime_config.py`（统一读取） |
| `tools/` | `tools`（目录） | 外部 API / 脚本实现层：行情 provider 客户端与研报 Node 脚本 |

与 **`tools/yanbaoke/`** 区分：`intel` 是 **Python 封装**；`tools/yanbaoke` 是 **Node 脚本 + SKILL**。

---

## `analysis/` 内文件

| 文件 | 职责 |
|------|------|
| `price_feeds.py` | 统一入口 `fetch_ohlcv(provider, …)`：仅做 symbol/interval 适配、provider 分发与 OHLCV 归一化 |
| `kline_metrics.py` | 股票/通用分析主干：SMA/Fib/趋势、威科夫背景、123 结构、`structure_filters_v1`、`time_stop_v1`、`mtf_v1`（辅周期可选）、`compute_ohlc_stats`、`format_*` 报告片段 |
| `crypto_kline_analysis.py` | CryptoTradeDesk 风格增强层：复用 `kline_metrics` 并叠加 MA 8/21/55 与 regime 字段（读 `config/analysis_defaults.yaml`） |
| `ledger_stats.py` | 读 PostgreSQL 台账（经 `load_journal_entries_for_stats` → `journal_ideas`），生成周/月统计、`breakdown_*d`（按 status / wyckoff_bias、时间止损过期挂单数）、可读 Markdown `trade_journal_readable.md` |
| `gold_api.py` | 贵金属辅助：品种映射、日线聚合等（不承载 provider 级 HTTP 请求实现） |
| `trade_journal.py` | 台账状态机：`watch/pending -> filled/expired`、`filled -> closed(tp/sl)/float_*`，以及去重辅助 |
| `journal_policy.py` | 台账写入策略：`min_journal_rr`、可选 `journal_quality`、加密 `swing` 候选构造（与 CryptoTradeDesk 思路对齐） |

---

## `intel/` 内文件

| 文件 | 职责 |
|------|------|
| `yanbaoke_client.py` | `search_reports_*`、`write_research_bundle`（`subprocess` 调 `search.mjs`） |

---

## `cli/` 内文件

| 文件 | 职责 |
|------|------|
| `stock_analysis.py` | 薄入口 CLI：参数解析后调用 `app.orchestrator.run(args)` |

---

## `app/` 内文件

| 文件 | 职责 |
|------|------|
| `orchestrator.py` | 主流程编排：选标的、拉主/辅周期、调分析引擎、组装 overview 与候选台账 |
| `report_writer.py` | 报告与总览写入：同日 prepend、`ai_overview` 槽位合并、历史时间戳文件清理 |
| `journal_service.py` | 台账服务：先更新旧条目再追加新候选（含 RR/质量门控）并刷新统计文件；调用 `persistence` 包写 PG |

---

## `persistence/` 内文件（PostgreSQL）

| 文件 | 职责 |
|------|------|
| `db.py` | 全局 SQLAlchemy `Engine`（`get_postgres_dsn` 有值时建池） |
| `sql_loader.py` | `load_sql` / `load_sql_text`：从仓库根 `sql/` 读 UTF-8（进程内缓存） |
| `journal_repository.py` | `JournalRepository` 协议 |
| `journal_repository_factory.py` | `get_journal_repository`、`load_journal_entries_for_stats` |
| `journal_repository_pg.py` | `journal_ideas` / `journal_events` 读写；大额 DML 见 `sql/journal/*.sql` |
| `account_service.py` | `account_ledger` / `account_positions`、MTM；显式 **`deposit_funds` / `withdraw_funds` / `adjust_funds`**（见 `scripts/account_cash_move.py`） |
| `paper_trade_service.py` | `paper_orders` / `paper_fills`；由 `journal_service` 在 filled/平仓后调用 |

---

## 配置与工具

| 路径 | 职责 |
|------|------|
| `config/market_config.json` | 标的列表、`default_symbols`、`market` / `data_symbol`；含 `CRYPTO`（如 `BTC_USDT`）与可选 `tags` |
| `config/analysis_defaults.yaml` | crypto 分析默认参数（MA 8/21/55、分形参数、RR 阈值等） |
| `sql/` | 运行期 DML 片段（`journal/*.sql`、`account/ledger_append_snapshot.sql`）；只读查询样例见 `docs/SQL_AI_REFERENCE.md` |
| `scripts/account_cash_move.py` | CLI：账户 `deposit` / `withdraw` / `adjust`（写 `account_ledger`） |
| `tools/yanbaoke/scripts/search.mjs` | 研报客搜索（无 Key 可搜） |
| `tools/yanbaoke/scripts/download.mjs` | 研报下载（需 Key） |
| `tools/yanbaoke/SKILL.md` | 研报客技能说明 |

---

## 行情 Provider 客户端（`tools/`）

| 路径 | 职责 |
|------|------|
| `tools/tickflow/client.py` | tickflow 外部 API 调用、超时与基础异常封装 |
| `tools/gateio/client.py` | gateio 外部 API 调用、超时与基础异常封装 |
| `tools/goldapi/client.py` | goldapi 外部 API 调用、超时与基础异常封装 |
| `tools/common/errors.py` | ProviderError / ParseError / RateLimitError 等通用错误类型 |

---

## 产物目录（非包）

| 路径 | 内容 |
|------|------|
| `output/<provider>/<market>/<本地日期>/` | `ai_brief.md`、`ai_overview.json`、`full_report.md`（K 线会话） |
| `output/research/<provider>/<market>/<本地日期>/` | `stock_analysis.py --with-research` 研报落盘：`*_research.json` / `*_research.md` |
| `output/research/<本地日期>/` | **仅** `cli/yb_search.py` 默认输出（无 provider/market 分桶） |
| `output/<provider>/<market>/<本地日期>/journal` | 台账统计锚点（无扩展名）；台账行在 PostgreSQL `journal_ideas`；同目录含 `trade_journal_stats_latest.md`、`trade_journal_readable.md` 等统计快照 |

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
