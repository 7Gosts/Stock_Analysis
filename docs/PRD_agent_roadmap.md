# Stock_Analysis 项目速览与路线图

本文档给新接手的同事、外部协作者和后续 AI 一个“先看这篇就能知道项目大概在做什么”的入口。

目标不是覆盖全部细节，而是快速回答下面几个问题：

1. 这个项目是什么，不是什么。
2. 当前主链路怎么跑。
3. 仓库里各层分别负责什么。
4. 现在已经做到哪一步，下一阶段准备怎么收口。

**合规口径**：本项目当前定位为技术分析、研究检索、程序化演示与 Agent 编排，不构成投资建议；默认不做自动实盘交易。

---

## 一、项目一句话说明

`Stock_Analysis` 是一个以 **多资产行情分析 + 本地研究产物 + Agent 统一入口** 为核心的项目。

当前主要能力是：

1. 拉取多市场 OHLCV / 行情数据。
2. 做技术结构分析，包括趋势、Fib、Wyckoff 背景、123 结构、多周期共振等。
3. 生成简报、总览 JSON、全文报告和台账快照。
4. 可选接入研报检索，给出“叙事线索”，但不把研报摘要冒充价格触发依据。
5. 通过统一 Agent Core，把 CLI、HTTP、飞书三种入口逐步收敛到同一套请求处理链。

它不是一个“全自动交易系统”，也不是一个完整的基本面投研平台。当前最成熟的是 **技术分析 + 研究线索检索 + 对话式访问**。

---

## 二、项目当前定位

### 2.1 已经清晰的定位

项目当前有两个并行但边界清楚的方向：

1. **分析流水线**：从行情与研究线索产出结构化分析结果。
2. **Agent 产品化**：把这些分析能力包装成统一的智能体核心，再接 CLI、HTTP、飞书等不同入口。

### 2.2 当前不做的事情

这些内容要么未做，要么刻意不放在首期范围：

1. 自动实盘下单。
2. 账户级组合优化、VaR、蒙特卡洛、回撤引擎。
3. 深度基本面估值模型。
4. 实时逐笔资金流或“主力资金净流入”类官方口径模拟。

### 2.3 为什么这样定义边界

因为当前项目最强的是“结构化行情分析”和“弱研究检索”，这两者适合先做成稳定的 Agent 核心。执行层、投顾层、组合层如果过早混进来，会把边界打乱，也会让飞书机器人这类入口承担不该承担的职责。

---

## 三、当前产品形态

项目今天已经不是一个单纯的 CLI 脚本仓库，而是三种入口共用一套能力：

| 入口 | 用途 | 当前状态 |
|------|------|----------|
| CLI | 批量分析、单标的分析、离线产物生成 | 最成熟 |
| HTTP API | 给上层服务或对话入口调用统一分析能力 | 已可用 |
| 飞书 Bot | 对话式访问 Agent 能力 | 已接通，仍在收口职责 |

因此现在更准确的理解应该是：

**项目本体是 Agent Core + 分析流水线；飞书只是其中一个 transport adapter。**

---

## 四、核心能力地图

### 4.1 行情与技术分析

这是仓库当前最稳定的一层。

主要能力包括：

1. 多 provider 行情接入：`tickflow`、`gateio`、`goldapi`
2. K 线结构分析
3. Fib 区间与关键位
4. Wyckoff 背景过滤
5. 123 结构与候选计划价
6. 多周期信息汇总
7. 简报 / 全文 / JSON 总览生成

这部分主要落在：

1. `analysis/`
2. `tools/<provider>/client.py`
3. `cli/stock_analysis.py`

### 4.2 研究检索与叙事线索

当前支持接入研报检索，作为“观点线索”和“二次验证材料”的来源。

这里的原则已经比较明确：

1. 研报只提供叙事证据和观点分歧。
2. 研报不直接生成具体 entry / stop / tp。
3. 研报摘要不能冒充价格触发逻辑。

相关模块主要是：

1. `intel/`
2. `tools/yanbaoke/`
3. `cli/yb_search.py`

### 4.3 对话式 Agent 能力

这部分是当前快速演进的重点。

项目正在把原来偏飞书定制的逻辑，收口成统一 Agent Core：

1. 输入统一为 `AgentRequest`
2. 中间统一做路由、追问解析、本地 RAG、执行器分发
3. 输出统一为 `AgentResponse`

目标是 CLI、HTTP、飞书都走同一条主链路，而不是各自维护一套逻辑。

主要代码在：

1. `app/agent_core.py`
2. `app/agent_schemas.py`
3. `app/planner.py`
4. `app/agent_facade.py`
5. `app/feishu_adapter.py`
6. `app/api_server.py`

---

## 五、当前架构理解方式

### 5.1 分层理解

如果只看一遍代码，推荐用下面这套理解方式：

| 层 | 作用 | 代表路径 |
|----|------|----------|
| 数据源层 | 对接外部行情、研究、飞书等系统 | `tools/`、`intel/` |
| 分析层 | 指标、结构、台账策略、价格汇聚 | `analysis/` |
| 核心编排层 | 统一 Agent 请求、路由、执行器编排、writer | `app/` |
| 入口层 | CLI、HTTP、飞书调用入口 | `cli/`、`app/api_server.py`、`app/feishu_adapter.py` |
| 配置层 | 市场配置、默认参数、LLM/provider 配置 | `config/` |
| 产物层 | 报告、JSON、研究落盘、历史记忆 | `output/` |

### 5.2 当前推荐的主链路心智模型

可以把系统理解成下面这条链：

`用户输入 / CLI 命令`
-> `AgentRequest 或 CLI 参数`
-> `planner 做意图识别`
-> `agent_core / agent_facade 选择执行路径`
-> `analysis + research + rag 产出 facts_bundle`
-> `writer 生成最终回复或报告`
-> `落盘 output / 返回给 HTTP / 发回飞书`

### 5.3 飞书在架构里的位置

飞书不是项目本体，只是入口适配层。

飞书侧现在只应负责：

1. 收消息
2. 去重
3. 取最近历史
4. 构造统一请求
5. 发送统一响应

飞书不应继续承担：

1. 主路由策略
2. 平台专属业务判断
3. 本地 RAG 决策
4. 事实优先级判断

这也是当前重构方向的核心之一。

---

## 六、项目运行时的三层事实来源

当前对话 Agent 正在逐步收口成“三层事实模型”：

### 第一层：会话状态

用于解决“这个、它、上一轮那个标的”这类追问问题。

代表模块：

1. `app/session_state.py`
2. `app/followup_resolver.py`

### 第二层：本地 RAG / 分析产物

这是事实主源，用于回答上一轮的结构、关键位、触发状态等问题。

代表模块：

1. `app/rag_index.py`
2. `output/` 下的 `ai_overview.json`、`full_report.md`、研究产物

### 第三层：飞书历史 / 聊天历史

只用于补语境和风格，不作为价格事实主源。

代表模块：

1. `app/memory_store.py`
2. `app/feishu_adapter.py`

这三层的优先级已经很明确：

**结构化产物优先于聊天历史，聊天历史不能覆盖本地事实。**

---

## 七、当前已经完成的关键重构

如果别人想快速知道“这个项目最近发生了什么变化”，可以先看这几个点：

### 7.1 统一 Agent Core 方向已经确立

项目不再把飞书机器人当成系统中心，而是明确要把项目本体做成可复用的 Agent Core。

### 7.2 飞书研究 / 板块查询路由已经单独收口

之前“查研报/查板块/查归属”容易掉到 `clarify`。目前已经新增独立 tool 方向，并在 router prompt 中强化对应意图。

### 7.3 空回复问题已被识别为架构问题，不只是 bug

飞书不回消息的根因，不只是发送失败，而是某些 `clarify` 路径会产出空文本。现在方向已经明确：所有分支都必须保证可见回复。

### 7.4 LLM 配置入口已经开始统一

配置已经从过去只看顶层 `deepseek`，过渡到 `llm.providers.*` 的统一入口。默认 provider 仍是 DeepSeek，但方向是 provider-agnostic。

---

## 八、当前仍在推进的主线

### 8.1 把 `tools/deepseek/client.py` 收口成通用 LLM client

这是当前最明确的下一阶段工程目标之一。

问题不在于文件名不好看，而在于它实际已经承担了：

1. OpenAI-compatible HTTP 调用
2. 飞书路由工具定义
3. 路由执行
4. grounded writer / narrative writer

但名字和异常名仍然强绑定 DeepSeek，容易误导后续维护者。

### 8.2 把飞书里的个性化默认值移出 YAML

`default_symbol`、`default_interval`、`short_term_interval` 这类字段太像“prompt policy / route context”，不该长期停留在 transport config。

下一阶段目标是把这些值收口到：

1. session state
2. market config
3. planner / router policy constants

而不是继续在 `feishu:` 配置段里堆个性化字段。

### 8.3 统一三种入口的行为一致性

CLI、HTTP、飞书三条链当前已经部分统一，但还没有完全做到“同样问题、同样事实、同样输出原则”。

后续仍需要继续收口：

1. 默认值来源
2. writer 策略
3. route context
4. 错误和澄清行为

---

## 九、给新同事的最短阅读顺序

如果只给一个新接手的人 20 分钟，建议按这个顺序看：

1. 本文，先知道项目定位和主链路。
2. `README.md`，看命令和目录级说明。
3. `AGENTS.md`，看数据源边界和角色模式。
4. `docs/NAMESPACE.md`，按文件名查模块职责。
5. `app/agent_core.py`、`app/planner.py`、`app/feishu_adapter.py`，理解当前 Agent 主链路。
6. `cli/stock_analysis.py` 和 `analysis/`，理解最成熟的分析流水线。

---

## 十、快速路径索引

| 想看什么 | 先看哪里 |
|----------|----------|
| 项目整体入口 | `README.md`、本文 |
| Agent 主链路 | `app/agent_core.py`、`app/planner.py` |
| 飞书入口 | `app/feishu_adapter.py` |
| HTTP 服务 | `app/api_server.py` |
| 技术分析核心 | `analysis/kline_metrics.py`、`analysis/price_feeds.py` |
| 研究检索 | `cli/yb_search.py`、`intel/`、`tools/yanbaoke/` |
| 本地事实 / RAG | `app/rag_index.py` |
| 会话状态 / 追问 | `app/session_state.py`、`app/followup_resolver.py` |
| 配置入口 | `config/runtime_config.py`、`config/analysis_defaults.yaml` |
| 当前重构方向 | `docs/AGENT_CORE_UNIFICATION_PLAN.md`、`docs/GENERIC_LLM_CLIENT_REFACTOR_EXECUTION_PROMPT.md` |

---

## 十一、项目现阶段的简短判断

如果要用一句更工程化的话概括当前状态：

**这是一个已经从“多市场分析脚本仓库”演进到“分析流水线 + 统一 Agent Core”阶段的项目；分析能力已经可用，Agent 架构正在从飞书中心化走向核心统一化。**

这句话基本可以概括今天的项目状态。

---

## 十二、后续维护建议

本文建议长期保持为“项目总览入口”，不要再回到纯需求脑图文档的写法。

后续更新时，优先维护这三类信息：

1. 项目当前定位是否变化。
2. 主链路和职责边界是否变化。
3. 下一阶段工程主线是否变化。

更细的接口、公式、字段、命名空间细节，分别放在其他文档，不要把本文写成第二份 README 或第二份 NAMESPACE。

*文档版本：2026-05，按当前仓库实现与重构方向整理。*

本文档给新接手的同事、外部协作者和后续 AI 一个“先看这篇就能知道项目大概在做什么”的入口。

目标不是覆盖全部细节，而是快速回答下面几个问题：

1. 这个项目是什么，不是什么。
2. 当前主链路怎么跑。
3. 仓库里各层分别负责什么。
4. 现在已经做到哪一步，下一阶段准备怎么收口。

**合规口径**：本项目当前定位为技术分析、研究检索、程序化演示与 Agent 编排，不构成投资建议；默认不做自动实盘交易。

---

## 一、项目一句话说明

`Stock_Analysis` 是一个以 **多资产行情分析 + 本地研究产物 + Agent 统一入口** 为核心的项目。

当前主要能力是：

1. 拉取多市场 OHLCV / 行情数据。
2. 做技术结构分析，包括趋势、Fib、Wyckoff 背景、123 结构、多周期共振等。
3. 生成简报、总览 JSON、全文报告和台账快照。
4. 可选接入研报检索，给出“叙事线索”，但不把研报摘要冒充价格触发依据。
5. 通过统一 Agent Core，把 CLI、HTTP、飞书三种入口逐步收敛到同一套请求处理链。

它不是一个“全自动交易系统”，也不是一个完整的基本面投研平台。当前最成熟的是 **技术分析 + 研究线索检索 + 对话式访问**。

---

## 二、项目当前定位

### 2.1 已经清晰的定位

项目当前有两个并行但边界清楚的方向：

1. **分析流水线**：从行情与研究线索产出结构化分析结果。
2. **Agent 产品化**：把这些分析能力包装成统一的智能体核心，再接 CLI、HTTP、飞书等不同入口。

### 2.2 当前不做的事情

这些内容要么未做，要么刻意不放在首期范围：

1. 自动实盘下单。
2. 账户级组合优化、VaR、蒙特卡洛、回撤引擎。
3. 深度基本面估值模型。
4. 实时逐笔资金流或“主力资金净流入”类官方口径模拟。

### 2.3 为什么这样定义边界

因为当前项目最强的是“结构化行情分析”和“弱研究检索”，这两者适合先做成稳定的 Agent 核心。执行层、投顾层、组合层如果过早混进来，会把边界打乱，也会让飞书机器人这类入口承担不该承担的职责。

---

## 三、当前产品形态

项目今天已经不是一个单纯的 CLI 脚本仓库，而是三种入口共用一套能力：

| 入口 | 用途 | 当前状态 |
|------|------|----------|
| CLI | 批量分析、单标的分析、离线产物生成 | 最成熟 |
| HTTP API | 给上层服务或对话入口调用统一分析能力 | 已可用 |
| 飞书 Bot | 对话式访问 Agent 能力 | 已接通，仍在收口职责 |

因此现在更准确的理解应该是：

**项目本体是 Agent Core + 分析流水线；飞书只是其中一个 transport adapter。**

---

## 四、核心能力地图

### 4.1 行情与技术分析

这是仓库当前最稳定的一层。

主要能力包括：

1. 多 provider 行情接入：`tickflow`、`gateio`、`goldapi`
2. K 线结构分析
3. Fib 区间与关键位
4. Wyckoff 背景过滤
5. 123 结构与候选计划价
6. 多周期信息汇总
7. 简报 / 全文 / JSON 总览生成

这部分主要落在：

1. `analysis/`
2. `tools/<provider>/client.py`
3. `cli/stock_analysis.py`

### 4.2 研究检索与叙事线索

当前支持接入研报检索，作为“观点线索”和“二次验证材料”的来源。

这里的原则已经比较明确：

1. 研报只提供叙事证据和观点分歧。
2. 研报不直接生成具体 entry / stop / tp。
3. 研报摘要不能冒充价格触发逻辑。

相关模块主要是：

1. `intel/`
2. `tools/yanbaoke/`
3. `cli/yb_search.py`

### 4.3 对话式 Agent 能力

这部分是当前快速演进的重点。

项目正在把原来偏飞书定制的逻辑，收口成统一 Agent Core：

1. 输入统一为 `AgentRequest`
2. 中间统一做路由、追问解析、本地 RAG、执行器分发
3. 输出统一为 `AgentResponse`

目标是 CLI、HTTP、飞书都走同一条主链路，而不是各自维护一套逻辑。

主要代码在：

1. `app/agent_core.py`
2. `app/agent_schemas.py`
3. `app/planner.py`
4. `app/agent_facade.py`
5. `app/feishu_adapter.py`
6. `app/api_server.py`

---

## 五、当前架构理解方式

### 5.1 分层理解

如果只看一遍代码，推荐用下面这套理解方式：

| 层 | 作用 | 代表路径 |
|----|------|----------|
| 数据源层 | 对接外部行情、研究、飞书等系统 | `tools/`、`intel/` |
| 分析层 | 指标、结构、台账策略、价格汇聚 | `analysis/` |
| 核心编排层 | 统一 Agent 请求、路由、执行器编排、writer | `app/` |
| 入口层 | CLI、HTTP、飞书调用入口 | `cli/`、`app/api_server.py`、`app/feishu_adapter.py` |
| 配置层 | 市场配置、默认参数、LLM/provider 配置 | `config/` |
| 产物层 | 报告、JSON、研究落盘、历史记忆 | `output/` |

### 5.2 当前推荐的主链路心智模型

可以把系统理解成下面这条链：

`用户输入 / CLI 命令`
-> `AgentRequest 或 CLI 参数`
-> `planner 做意图识别`
-> `agent_core / agent_facade 选择执行路径`
-> `analysis + research + rag 产出 facts_bundle`
-> `writer 生成最终回复或报告`
-> `落盘 output / 返回给 HTTP / 发回飞书`

### 5.3 飞书在架构里的位置

飞书不是项目本体，只是入口适配层。

飞书侧现在只应负责：

1. 收消息
2. 去重
3. 取最近历史
4. 构造统一请求
5. 发送统一响应

飞书不应继续承担：

1. 主路由策略
2. 平台专属业务判断
3. 本地 RAG 决策
4. 事实优先级判断

这也是当前重构方向的核心之一。

---

## 六、项目运行时的三层事实来源

当前对话 Agent 正在逐步收口成“三层事实模型”：

### 第一层：会话状态

用于解决“这个、它、上一轮那个标的”这类追问问题。

代表模块：

1. `app/session_state.py`
2. `app/followup_resolver.py`

### 第二层：本地 RAG / 分析产物

这是事实主源，用于回答上一轮的结构、关键位、触发状态等问题。

代表模块：

1. `app/rag_index.py`
2. `output/` 下的 `ai_overview.json`、`full_report.md`、研究产物

### 第三层：飞书历史 / 聊天历史

只用于补语境和风格，不作为价格事实主源。

代表模块：

1. `app/memory_store.py`
2. `app/feishu_adapter.py`

这三层的优先级已经很明确：

**结构化产物优先于聊天历史，聊天历史不能覆盖本地事实。**

---

## 七、当前已经完成的关键重构

如果别人想快速知道“这个项目最近发生了什么变化”，可以先看这几个点：

### 7.1 统一 Agent Core 方向已经确立

项目不再把飞书机器人当成系统中心，而是明确要把项目本体做成可复用的 Agent Core。

### 7.2 飞书研究 / 板块查询路由已经单独收口

之前“查研报/查板块/查归属”容易掉到 `clarify`。目前已经新增独立 tool 方向，并在 router prompt 中强化对应意图。

### 7.3 空回复问题已被识别为架构问题，不只是 bug

飞书不回消息的根因，不只是发送失败，而是某些 `clarify` 路径会产出空文本。现在方向已经明确：所有分支都必须保证可见回复。

### 7.4 LLM 配置入口已经开始统一

配置已经从过去只看顶层 `deepseek`，过渡到 `llm.providers.*` 的统一入口。默认 provider 仍是 DeepSeek，但方向是 provider-agnostic。

---

## 八、当前仍在推进的主线

### 8.1 把 `tools/deepseek/client.py` 收口成通用 LLM client

这是当前最明确的下一阶段工程目标之一。

问题不在于文件名不好看，而在于它实际已经承担了：

1. OpenAI-compatible HTTP 调用
2. 飞书路由工具定义
3. 路由执行
4. grounded writer / narrative writer

但名字和异常名仍然强绑定 DeepSeek，容易误导后续维护者。

### 8.2 把飞书里的个性化默认值移出 YAML

`default_symbol`、`default_interval`、`short_term_interval` 这类字段太像“prompt policy / route context”，不该长期停留在 transport config。

下一阶段目标是把这些值收口到：

1. session state
2. market config
3. planner / router policy constants

而不是继续在 `feishu:` 配置段里堆个性化字段。

### 8.3 统一三种入口的行为一致性

CLI、HTTP、飞书三条链当前已经部分统一，但还没有完全做到“同样问题、同样事实、同样输出原则”。

后续仍需要继续收口：

1. 默认值来源
2. writer 策略
3. route context
4. 错误和澄清行为

---

## 九、给新同事的最短阅读顺序

如果只给一个新接手的人 20 分钟，建议按这个顺序看：

1. 本文，先知道项目定位和主链路。
2. `README.md`，看命令和目录级说明。
3. `AGENTS.md`，看数据源边界和角色模式。
4. `docs/NAMESPACE.md`，按文件名查模块职责。
5. `app/agent_core.py`、`app/planner.py`、`app/feishu_adapter.py`，理解当前 Agent 主链路。
6. `cli/stock_analysis.py` 和 `analysis/`，理解最成熟的分析流水线。

---

## 十、快速路径索引

| 想看什么 | 先看哪里 |
|----------|----------|
| 项目整体入口 | `README.md`、本文 |
| Agent 主链路 | `app/agent_core.py`、`app/planner.py` |
| 飞书入口 | `app/feishu_adapter.py` |
| HTTP 服务 | `app/api_server.py` |
| 技术分析核心 | `analysis/kline_metrics.py`、`analysis/price_feeds.py` |
| 研究检索 | `cli/yb_search.py`、`intel/`、`tools/yanbaoke/` |
| 本地事实 / RAG | `app/rag_index.py` |
| 会话状态 / 追问 | `app/session_state.py`、`app/followup_resolver.py` |
| 配置入口 | `config/runtime_config.py`、`config/analysis_defaults.yaml` |
| 当前重构方向 | `docs/AGENT_CORE_UNIFICATION_PLAN.md`、`docs/GENERIC_LLM_CLIENT_REFACTOR_EXECUTION_PROMPT.md` |

---

## 十一、项目现阶段的简短判断

如果要用一句更工程化的话概括当前状态：

**这是一个已经从“多市场分析脚本仓库”演进到“分析流水线 + 统一 Agent Core”阶段的项目；分析能力已经可用，Agent 架构正在从飞书中心化走向核心统一化。**

这句话基本可以概括今天的项目状态。

---

## 十二、后续维护建议

本文建议长期保持为“项目总览入口”，不要再回到纯需求脑图文档的写法。

后续更新时，优先维护这三类信息：

1. 项目当前定位是否变化。
2. 主链路和职责边界是否变化。
3. 下一阶段工程主线是否变化。

更细的接口、公式、字段、命名空间细节，分别放在其他文档，不要把本文写成第二份 README 或第二份 NAMESPACE。

*文档版本：2026-05，按当前仓库实现与重构方向整理。*
