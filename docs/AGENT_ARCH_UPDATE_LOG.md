# 聊天 Agent 架构改造 — 更新记录

本文记录仓库内 **Agent 编排与执行链** 的变更摘要，便于查阅与 Code Review。设计条文仍以 `docx/聊天Agent架构改造设计计划.md` 为准。

## 2026-05-15

### 总览

- **入口**：`app/agent_core.handle_request` — 流程为 **intent 管道（可选）→ `plan_user_message` → `run_post_route_chat_graph`**，各平台不再分叉走独立执行实现。
- **编排**：`app/agent_graph.py` — LangGraph：`capability` → `compose` → `update_session` → `compact`。
- **状态**：`app/agent_state.py` — `ChatPostRouteState`（含 `skip_compose_llm` 等），与单次分析用 `langgraph_flow` 区分。
- **Facade**：**已移除** `app/agent_facade.py`；原 `handle_user_request` 兼容入口若仍存在，以 `agent_core` 内文档与实现为准。

### Planner 与意图

- **`infer_task_type_from_text`**：已去掉与 intent 管道重复的 **`_SIM_ACCOUNT_PAT`** 抢先 `sim_account` 逻辑，避免双轨。
- **追问**：`looks_like_followup` / `resolve_followup_target` / `detect_followup_route` 等集中在 **`app/intent_detectors.py`**（**已移除**独立 `followup_resolver` 模块时的内联实现；若后续再拆模块，可从此文件迁出）。

### 共享能力模块

| 模块 | 说明 |
|------|------|
| `app/capabilities/quote_facts.py` | `run_quote_facts_bundle` |
| `app/capabilities/research_facts.py` | `build_research_facts_bundle` |
| `app/capabilities/compare_facts.py` | `run_compare_facts_bundle` |
| `app/route_chat_handlers.py` | `build_chat_handle_result`（chat 路由与旧 facade 返回形状对齐） |

### 其它行为

- **`SessionState`**：延续 `last_facts_bundle`、`last_display_preferences`、`history_version`、`compacted_summary` 等字段（见 `app/session_state.py`）。
- **飞书**：`app/feishu_adapter.get_recent_messages` 支持按 **`AGENT_RECENT_MESSAGE_KEEP_PAIRS`**（默认 12 对消息）截断，控制路由上下文长度。
- **错误映射**：追问缺产物等异常在图内抛出约定文案后，由 **`agent_core._classify_execute_exception`** 归类（如 `followup_output_missing`）。

### 测试

- `tests/` 子包补充 **`__init__.py`**，便于 `unittest discover -s tests` 递归发现 `tests/unit` 等目录。
- 部分集成测试在 **`AGENT_UNIFIED_GRAPH=0`** 下运行以稳定 mock（若开关仍存在）；默认路径以统一图为准。

### 已知后续项（未承诺排期）

- `compact_node` 的 **LLM 摘要**（需单独成本开关）。
- 将 **路由节点** 迁入 LangGraph 与 repair loop 深度整合（单独里程碑）。
