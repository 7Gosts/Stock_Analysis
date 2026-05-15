# Agent 统一数据能力层设计（行情 / 研报 / 模拟账户）

本文是对“AI 能直接看行情、读研报、查模拟账户”这三类能力的统一设计说明。目标不是临时给数据库再开一个口子，而是把它们收敛成同一层能力抽象，让 CLI、HTTP、飞书和未来的 Agent tool 调用都复用同一套读写边界。

相关现状代码：

- `app/agent_tools.py` 目前只暴露了 `fetch_analysis_bundle` 一个行情工具。
- `docs/SQL_AI_REFERENCE.md` 已经整理了常用只读 SQL，但它还是“手册”，不是运行时可复用查询层。
- `persistence/account_service.py`、`persistence/paper_trade_service.py` 已经负责模拟账户、持仓、纸订单和成交的写入与部分读取。
- `app/feishu_adapter.py` 已经统一走 `app.agent_core.handle_request()`，说明飞书本身不需要再单独造一条数据库链路。

---

## 1. 这次暴露出的真实问题

用户问“看看当前资金额度，有多少在进行的订单”时，最后虽然能查出来，但中间链路太长。根因不是 PostgreSQL 不可查，而是系统里还没有一套面向 Agent 的统一数据能力层。

当前痛点分为四类：

1. `app/agent_tools.py` 只有行情工具，没有账户/订单/成交查询工具，导致 Agent 无法像“看行情”一样直接调用能力。
2. SQL 已经沉淀在 `docs/SQL_AI_REFERENCE.md` 和 `persistence/` 代码里，但没有统一的查询注册表与结果格式器，AI 每次都要重新定位表、拼 SQL、解释结果。
3. 飞书虽然已经复用 `agent_core`，但由于没有“模拟账户能力域”，账户查询无法像 analysis/research 一样成为一等能力，只能旁路处理。
4. 当前 Python 侧查询数据库依赖 `SQLAlchemy + psycopg`；如果运行时没进 `.venv`，会退化成“`psql` 能查、Python client 不能查”的操作问题。这是环境问题，不是架构应当引入另一种语言的理由。

一句话概括：

**现在缺的不是 SQL，也不是 API，而是“统一能力层 + 可复用查询处理器”。**

---

## 2. 设计目标

新的结构应同时满足下面四个目标：

1. AI 在被问到行情、研报、模拟账户时，都能像调用工具一样直接拿到结构化结果。
2. 飞书、CLI、HTTP 都复用同一套能力层，不再各自拼 SQL、各自写格式化逻辑。
3. 模拟账户管理在架构上与“行情分析”“研报解读”同级，不表现为后加特例。
4. 保持边界清晰：查询走只读 read model，状态变更走 command handler，不让 AI 直接执行自由 SQL 写操作。

---

## 3. 总体方案：统一能力层，而不是单独补一个 DB 查询入口

建议把系统能力抽象成三个并列 capability：

1. `market`：查看行情与技术结构
2. `research`：查看研报线索与叙事摘要
3. `sim_account`：查看模拟账户资金、持仓、挂单、成交与一致性状态

三者共用同一个返回契约：

```python
CapabilityResult = {
    "domain": "market | research | sim_account",
    "intent": "overview | positions | orders | fills | report | snapshot ...",
    "summary": "给用户的一段短摘要",
    "tables": [...],
    "metrics": {...},
    "evidence_sources": [...],
    "meta": {...},
}
```

这层不关心调用方是谁。对飞书、CLI、HTTP、LangGraph tool 来说，它们都只是 capability consumer。

推荐目录形态：

```text
app/
  capabilities/
    base.py
    market_capability.py
    research_capability.py
    sim_account_capability.py
  query_engine/
    registry.py
    executor.py
    formatter.py
    commands.py

sql/
  queries/
    account/
      latest_balances.sql
      open_positions.sql
      active_ideas.sql
      recent_orders.sql
      recent_fills.sql
      order_health.sql
```

### 为什么这是“像一开始就这么设计”的结构

因为它不是“analysis 旁边再加一个 account.py”，而是把系统统一理解为三类数据能力：

- 行情能力读取 market data
- 研报能力读取 research data
- 模拟账户能力读取 account / order / fill / journal data

这三个域都通过同一层 capability interface 暴露，Agent 只决定“本轮要调哪个能力”，而不是再区分“这是原生功能还是后来补的功能”。

---

## 4. Query / Command 双通道

如果要让“模拟账户管理”看起来整齐，最重要的是不要让 AI 直接拿自由 SQL 乱查乱写。

建议采用轻量的 Query / Command 双通道。

### 4.1 Query 通道：给 AI 和飞书的只读能力

这一层负责“查”。

核心原则：

1. AI 不直接生成任意 SQL。
2. 运行时只允许调用命名查询（named query）。
3. 每个查询都配套参数校验和结果格式器。

建议的查询规格：

```python
@dataclass(frozen=True)
class SqlQuerySpec:
    name: str
    sql_path: str
    access: Literal["read"]
    params_schema: type[BaseModel]
    formatter: Callable[[list[dict[str, Any]], dict[str, Any]], dict[str, Any]]
```

执行流程：

1. Agent 选择 `sim_account` 能力。
2. 能力层根据 intent 选择命名查询。
3. `query_engine.executor` 通过 `persistence.db.get_sqlalchemy_engine()` 执行 SQL。
4. `formatter` 产出统一的 `CapabilityResult`。
5. `agent_core` / `agent_facade` / `agent_tools` / `feishu_adapter` 共用结果。

### 4.2 Command 通道：给明确动作的状态变更

这一层负责“改”。

对 AI 来说，不建议开放“任意 SQL 的增删改查”。原因很简单：

1. 账户、持仓、订单、成交是业务状态，不是文档表。
2. 当前代码已经有 `account_service.py` 这样的领域服务，应该复用它，而不是让 AI 直写 DML。
3. 后续如果做飞书里的“入金”“调账”“关闭模拟仓位”“取消观察单”等，都应该走命令处理器并保留审计。

建议的 command 入口示例：

- `account.deposit_funds`
- `account.withdraw_funds`
- `account.adjust_funds`
- `journal.expire_idea`
- `paper.cancel_pending_order`

这些命令在实现上应优先复用现有服务：

- `persistence.account_service.deposit_funds`
- `persistence.account_service.withdraw_funds`
- `persistence.account_service.adjust_funds`
- `app.journal_service`
- `persistence.paper_trade_service`

对话入口的第一阶段只开放 Query，不开放 Command。这样既能满足“看资产/挂单/成交”的主需求，也能先把边界做稳。

---

## 5. Agent 工具层应该怎么长

当前 `app/agent_tools.py` 只有一个 `fetch_analysis_bundle`，这就是为什么“看行情”像工具，“看账户”却像排障。

建议把工具层改成三个同级工具：

```python
view_market_snapshot(...)
view_research_digest(...)
view_sim_account_state(scope="overview", account_id=None, symbol=None, limit=20)
```

其中 `view_sim_account_state` 可以支持这些 scope：

1. `overview`：最新余额、可用资金、已用保证金、未实现盈亏、权益
2. `positions`：当前未平仓持仓
3. `active_ideas`：`journal_ideas` 中 `watch/pending/filled` 的活动交易想法
4. `orders`：最近纸订单，必要时区分 pending / filled
5. `fills`：最近成交
6. `health`：paper order/fill 对账状态

为什么先用一个账户工具而不是六个小工具：

1. 用户问法通常是组合式的，比如“看看资金额度，有多少在进行的订单”。
2. 单次工具调用可以同时返回 overview + active_ideas + positions，节省 tool round-trip 和 token。
3. 如果后续发现 LLM 在 scope 选择上不稳定，再拆成多个小工具也不晚。

---

## 6. 飞书如何复用，不再单独造链路

这块反而最简单。

`app/feishu_adapter.py` 已经走 `handle_request()`，所以飞书并不需要一套额外的数据库架构。真正需要变的是 planner 和 facade：

1. planner 能识别“账户余额 / 持仓 / 挂单 / 成交 / 模拟账户”意图。
2. `agent_core` 把该意图路由到 `sim_account` capability。
3. `agent_facade` 或 `agent_tools` 调用 `view_sim_account_state()`。
4. 飞书只是发送同一个 `CapabilityResult` 的人类可读摘要。

这意味着：

**AI 先具备“本地读账户”的能力，飞书天然复用，不需要先做 HTTP API 再让飞书绕一跳调用。**

---

## 7. 为什么当前更适合“直连数据库 + 本地查询层”，而不是再造 API

对当前仓库来说，优先级应该是：

1. **本地查询层**
2. 内部工具封装
3. 必要时再暴露 API

原因：

1. `persistence/` 已经持有数据库连接池和领域服务，再绕内部 HTTP 只会增加 hop。
2. 数据就在同仓库、同运行环境，本地读数据库比再造一个 account API 更直接。
3. Agent 最终需要的是结构化结果，不是 HTTP 形式本身。
4. 飞书和 CLI 最终都跑在 Python 进程内，直接复用 Python service 的耦合更低。

只有在以下场景，才值得再把它包一层 API：

1. 数据库访问需要和 Agent 运行时隔离权限。
2. 后续有独立前端或多服务部署，需要跨进程复用。
3. 需要做专门的审计、限流、RBAC。

在当前阶段，内部 API 不是最优先解。

---

## 8. 要不要换别的语言或现成库

### 8.1 不建议为了查库再换语言

当前主链路已经是 Python：`agent_core`、`planner`、`feishu_adapter`、`persistence` 全都在 Python 里。为了账户查询单独引入 Node 或 Go，只会带来：

1. 新的部署和依赖管理
2. 新的权限和连接配置
3. 新的序列化边界
4. 额外的错误面

这类成本，对当前问题没有必要。

### 8.2 推荐继续用的库

对当前项目，最合适的仍然是：

1. `SQLAlchemy Core`：保留现有 engine/pool，适合命名 SQL 和 typed result。
2. `psycopg[binary]`：作为 PostgreSQL driver，已经在 `requirements.txt` 中声明。
3. `Pydantic`：用于 query params 和 capability result schema。

### 8.3 可以考虑，但不是现在就要上的库

1. `asyncpg`：如果后续账户查询量很大、且 API 进入高并发 async 场景，再考虑。
2. `sqlglot`：如果后续真要支持更通用的 SQL 模板校验，可以做 read-only SQL AST 校验，但第一阶段没必要。
3. LangChain / LlamaIndex 的 SQL agent toolkit：可用于实验，但不建议直接作为生产主通路，因为它们更偏“让模型猜 SQL”，而不是“受控命名查询”。

### 8.4 关于这次 `psql` 能查、Python 不能查

那次现象的真实原因更像是解释器环境不一致：系统 `python3` 没进 `.venv`，于是 `psycopg` 没装进那个解释器。它说明要统一运行时环境，但不说明需要换语言或改成 API。

---

## 9. 推荐的第一阶段实施顺序

### P0：先把“查询”做成一等能力

目标：让 AI 和飞书都能稳定回答“当前资金额度、有多少进行中的订单、最近成交”这类问题。

实施项：

1. 新增 `sim_account` capability。
2. 新增 `query_engine.registry / executor / formatter`。
3. 把 `docs/SQL_AI_REFERENCE.md` 里的常用 SQL 落成 `sql/queries/account/*.sql` 命名查询。
4. 在 `app/agent_tools.py` 增加 `view_sim_account_state()`。
5. planner 增加账户相关意图识别。
6. `agent_facade` 直接消费 capability result，而不是绕到 analysis 分支。

### P1：统一 capability contract

目标：让 market / research / sim_account 都返回统一结果形状。

实施项：

1. 给行情快照和研报检索也套上 `CapabilityResult`。
2. writer / feishu formatter 只消费统一结果，不区分能力来源。
3. 在 `agent_core` 的 meta 中加入 `domain`、`intent`、`evidence_sources`。

### P2：再考虑 command handler

目标：让模拟账户管理不仅能“看”，也能做受控变更。

实施项：

1. 引入 `command registry`。
2. 对资金调整、观察单失效、模拟仓位关闭等动作做 typed command。
3. 加确认、审计日志和权限边界。

---

## 10. v1 命名查询建议

第一阶段建议先做下面这些查询名，已经足够覆盖大部分“查看模拟账户”的自然语言问法：

| query name | 作用 | 主要来源表 |
|------------|------|------------|
| `account.latest_balances` | 最新账户余额快照 | `account_ledger` |
| `account.open_positions` | 未平仓持仓 | `account_positions` |
| `account.active_ideas` | `watch/pending/filled` 的活动 idea | `journal_ideas` |
| `account.recent_orders` | 最近纸订单 | `paper_orders` |
| `account.recent_fills` | 最近成交 | `paper_fills` |
| `account.order_health` | order/fill 对账状态 | `paper_orders` + `paper_fills` + `journal_ideas` |

组合问法映射示例：

- “看看当前资金额度” -> `latest_balances`
- “有多少在进行的订单” -> `active_ideas`
- “最近成交了什么” -> `recent_fills`
- “模拟账户现在什么状态” -> `latest_balances + open_positions + active_ideas + order_health`

---

## 11. 结论

这个问题最优解不是“继续人工写 SQL”，也不是“为了查库再包一层 API”，更不是“单独给飞书写一个数据库功能”。

更合理的方向是：

1. 把行情、研报、模拟账户收敛成三个并列 capability。
2. 用命名 SQL + 参数 schema + formatter 做一个受控 query engine。
3. 用 command handler 承担未来真正的账户管理动作。
4. 让 AI、CLI、HTTP、飞书都复用同一套 capability result。

这样做之后，系统对外表现就会自然变成：

- 查看行情
- 市场研报解读
- 模拟账户管理

三者是同一架构下的三个能力域，而不是“先做了两个，后来又补了一个数据库查询”。