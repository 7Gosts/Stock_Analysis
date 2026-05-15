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
| `agent_core.py` | **统一 Agent Core 入口**：消费 AgentRequest -> planner 路由 -> 执行器 -> AgentResponse；记录容错状态（route_attempts、last_error_code、repair_history、termination_reason） |
| `agent_schemas.py` | **统一请求/响应 Schema**：AgentRequest、AgentResponse、AgentError、AgentErrorCode、AgentErrorStage、ERROR_CODE_DEFAULTS；定义结构化错误模型 |
| `planner.py` | **路由层**：只消费已标准化 route；输出统一协议（action=analyze 时内部统一使用 symbols=[]）；抛 AgentRoutingError 结构化错误 |
| `agent_facade.py` | **执行层**：调用 agent_facade 执行分析/报价/比较/追问；聚合 facts_bundle；处理飞书适配 |
| `session_state.py` | **会话状态层**：记录 last_symbols、route_attempts、last_error_code、repair_history、termination_reason；提供 record_error、record_success、record_final_termination、reset_route_attempts 方法 |
| `memory_store.py` | **飞书历史层**：只存储飞书消息文本，不存储结构化状态；用于指代消解和风格延续（不作为事实源） |
| `rag_index.py` | **本地 RAG 索引**：从 output/ 加载分析产物，提供 facts_for_followup、facts_for_research；作为事实主源 |
| `followup_resolver.py` | **追问解析**：从 session_state 获取上一轮标的、周期、产物路径；辅助追问路由 |
| `feishu_adapter.py` | **飞书入口适配**：收消息、去重、取历史、构造 AgentRequest、发送 AgentResponse；不承担路由或业务判断职责 |
| `api_server.py` | **HTTP API 服务**：提供 HTTP 入口，调用 agent_core 处理请求 |
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
| `docs/UNIFIED_DATA_CAPABILITY_ARCHITECTURE.md` | Agent 统一数据能力层设计：把行情、研报、模拟账户收敛成同级 capability，并定义命名 SQL 查询层 |
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

---

## Route Contract 与容错闭环

### action 与 task_type 两层概念（重要）

**这是当前最容易混淆的点**：

#### action（router 协议动作）

action 是 LLM router 输出的协议动作，只有以下几种：

| action | 含义 | 对应 LLM tool |
|--------|------|---------------|
| `analyze` | 行情分析请求 | `analyze_market` |
| `chat` | 闲聊/寒暄/引导 | `reply_chat` |
| `research` | 研报/机构观点检索 | `search_research` |
| `concept_board` | 板块/概念/归属查询 | `query_concept_board` |
| `followup` | 追问上一轮结果 | 会话状态推断（非 LLM tool） |

**注意**：`quote`、`compare`、`analysis` 不是 action，是 task_type。

#### task_type（业务语义分类）

task_type 是 planner 根据用户文本语义 + symbol 数量推断的业务分类：

| task_type | 含义 | 推断条件 |
|-----------|------|----------|
| `chat` | 闲聊 | action=chat |
| `quote` | 快速报价 | action=analyze + 单标的 + 用户文本含"现价/多少钱"语义 |
| `compare` | 多标的对比 | action=analyze + 多标的 + 用户文本含"谁更强/对比"语义 |
| `analysis` | 技术分析 | action=analyze + 默认分类 |
| `research` | 研报检索 | action=research 或 action=analyze + with_research=True + 研报语义 |
| `followup` | 追问 | 会话状态推断 |

**关键约束**：

1. `quote / compare / analysis` 都是 `action=analyze` 的子类型，由 planner 根据语义推断。
2. 不允许把 `quote / compare` 扩成新的 planner action。
3. `research` 既可以是 action（纯研报请求），也可以是 task_type（研报语义推断）。

### Route Contract（路由协议统一，已完成）

**原则**：协议统一发生在边界层，不发生在业务消费层。

LLM 输出的 tool call 经过 `_tool_calls_to_routed_dict()` normalize 后，内部协议统一为：

#### analyze（action）

```json
{
  "action": "analyze",
  "symbols": ["AU9999"],  // 统一使用 symbols 列表，不再使用 symbol 单数字段
  "interval": "1d",
  "provider": "goldapi",
  "question": "上海金今天走势",
  "with_research": false
}
```

**约束**：
- 单标的也必须是 `symbols` 长度为 1 的列表
- 下游代码只消费 `symbols`，不再处理 `symbol` 单数

#### research（action）

```json
{
  "action": "research",
  "keyword": "半导体",
  "symbol": ""  // 可为空字符串
}
```

#### chat（action）

```json
{
  "action": "chat",
  "chat_reply": "..."
}
```

#### followup（action）

```json
{
  "action": "followup",
  "followup_context": {...}
}
```

**关键点**：
- 协议统一发生在边界层
- 下游不再承担 shape 修复职责

### runtime clarify 状态（已完成）

**状态**：app runtime 中的 clarify 生命周期已经清理完成。

当前代码状态：
1. `TaskType` 已不再包含 `clarify`。
2. `AgentResponse` / adapter fallback 已统一为 chat-style fallback。
3. app 层不再保留 clarify 专用错误码或执行分支。

历史文档、旧讨论或外包汇报中仍可能出现 `clarify` 字样，但它们不再代表当前 runtime 主链路。

### 结构化错误模型（已完成）

**原则**：失败和拒绝不应该只是日志文本，而应该是状态机可以消费的结构化事件。

核心字段：
- `error_code`：错误码枚举（如 route_missing_symbols、followup_missing_symbol）
- `error_stage`：错误阶段（route / execute / infra / unknown）
- `recoverable`：是否可恢复（用于决定是否触发 reroute）
- `termination_reason`：最终终止原因（success / error_code / max_attempts_reached）

错误码第一批（已落地）：
1. `route_missing_symbols`
2. `route_invalid_symbol`
3. `route_missing_chat_reply`
4. `route_empty_message`
5. `route_unknown_action`
6. `followup_missing_symbol`
7. `followup_output_missing`
8. `execute_analysis_failed`
9. `execute_quote_failed`
10. `db_unavailable`
11. `analysis_backend_unavailable`
12. `rag_unavailable`
13. `unknown`

### 轻量容错闭环（已完成 route 层）

**原则**：最多一次 repair，不是无限 agent loop。

**当前状态**：

| 内容 | 状态 |
|------|------|
| session_state 容错字段 | 已完成 |
| AgentRoutingError 结构化错误 | 已完成 |
| route 层 reroute | **已完成** |
| 执行层错误细分 | **进行中**：第一轮 execute mapping 已收口；已区分 `db_unavailable`、`analysis_backend_unavailable`、`rag_unavailable`、`execute_provider_timeout`、`execute_writer_failed`、`followup_output_missing`，generic execute fallback 仍可能落到 `unknown` |
| 执行层错误接入 reroute | **未定**：待讨论 |

已实现内容：

1. `agent_core.py` 实现 `max_route_attempts = 2`（for 循环）
2. `_build_repair_recent_messages()` 构造修正提示，包含 error_code、termination_reason
3. 只对 `error_stage=route` 且 `recoverable=True` 的 route error 启用 reroute
4. 第二次 route 仍失败，返回 `termination_reason="max_route_attempts_reached"`

**关键边界**：

| 错误类型 | 是否接入 reroute | 当前行为 |
|----------|------------------|----------|
| recoverable route error | **是** | 第一次失败后构造修正提示，第二次 route |
| non-recoverable route error | **否** | 直接终止，返回 fallback + structured meta |
| 执行层错误（execute_* / infra_*） | **否** | 直接终止；第一轮 execute mapping 已收口，剩余未识别异常才回退到 `unknown` |

**不引入**：
- 多 Agent 协调
- 复杂 Task UI
- 长链计划模式
- 重型复杂度

### 会话状态容错字段（已完成）

`session_state.py` 新增字段：
- `last_symbols`：本轮 symbols（推荐使用，兼容 `last_symbol`）
- `route_attempts`：当前请求路由尝试次数
- `last_error_code`：最近一次错误码
- `repair_history`：修正历史（list[dict]）
- `termination_reason`：最终终止原因

**验证标准**：
- 一轮请求结束后，session state 能记录本轮 symbols
- 发生错误时，至少能记录 last_error_code
- 成功时能记录 termination_reason="success"

---

## 容错改造阶段状态

根据 [docs/AGENT_LOOP_TOLERANCE_REFACTOR.md](docs/AGENT_LOOP_TOLERANCE_REFACTOR.md)：

| 阶段 | 内容 | 状态 |
|------|------|------|
| 阶段 0 | 文档与契约先行 | **已完成** |
| 阶段 1 | 边界统一（route contract） | **已完成** |
| 阶段 2 | 结构化错误模型 | **已完成** |
| 阶段 3 | 最小 reroute loop（route 层） | **已完成** |
| 阶段 3.1 | 执行层错误细分 | **进行中** |
| 阶段 4 | 生命周期 hooks 与审计 | **暂缓** |

**当前优先级**：完成阶段 3.1 的执行层错误细分。

当前判断：阶段 3.1 已完成第一轮 execute mapping 收口，后续重点是继续补齐 execute 子原因并压缩 `unknown`。
