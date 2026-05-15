from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config.runtime_config import get_analysis_config, get_llm_runtime_settings
from analysis.beijing_time import default_review_time_for_interval, now_beijing_str


class LLMClientError(RuntimeError):
    """LLM client 调用异常（provider-agnostic）。"""
    pass


# OpenAI-compatible API：使用 response_format=json_object 时，messages 全文须出现子串 "json"（见 API 报错 invalid_request_error）
_JSON_OBJECT_SYSTEM_SUFFIX = "\n\n(json: Your entire reply must be one JSON object.)"


def _base_url() -> str:
    settings = get_llm_runtime_settings()
    url = str(settings.get("base_url") or "").strip()
    if url:
        return url.rstrip("/")
    provider = str(settings.get("provider") or "deepseek").strip().lower()
    if provider == "deepseek":
        return "https://api.deepseek.com"
    raise LLMClientError("缺少 LLM base_url（可通过 LLM_BASE_URL、<PROVIDER>_BASE_URL 或 YAML llm.providers.<provider>.base_url 配置）。")


def _model_name() -> str:
    settings = get_llm_runtime_settings()
    model = str(settings.get("model") or "").strip()
    if model:
        return model
    provider = str(settings.get("provider") or "deepseek").strip().lower()
    if provider == "deepseek":
        return "deepseek-v4-flash"
    raise LLMClientError("缺少 LLM model（可通过 LLM_MODEL、<PROVIDER>_MODEL 或 YAML llm.providers.<provider>.model 配置）。")


def _api_key() -> str:
    settings = get_llm_runtime_settings()
    key = str(settings.get("api_key") or "").strip()
    if not key:
        raise LLMClientError(
            "缺少 LLM API Key（可通过 LLM_API_KEY、<PROVIDER>_API_KEY 或 YAML llm.providers.<provider>.api_key 配置）。"
        )
    return key


def _resolved_temperature(default: float) -> float:
    settings = get_llm_runtime_settings()
    provider_temperature = settings.get("temperature")
    if provider_temperature is None:
        return float(default)
    return float(provider_temperature)


def _feishu_router_prompt_cfg() -> dict[str, Any]:
    cfg = get_analysis_config()
    node = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    return node if isinstance(node, dict) else {}


# Router policy 常量（不再从 YAML 配置读取）
ROUTER_POLICY = {
    "allowed_intervals": ["15m", "30m", "1h", "4h", "1d"],
    "default_interval": "4h",
    "term_to_interval": {
        "短线": "4h",
        "超短": "1h",
        "日内短线": "1h",
    },
}


def _feishu_short_term_interval() -> str:
    """飞书：用户说「短线」等且未明确周期时使用的默认 interval。

    注：已迁移到 router policy 常量，不再从 YAML 配置读取。
    """
    return ROUTER_POLICY["term_to_interval"].get("短线", "4h")


def _feishu_router_interval_instruction(*, short_iv: str) -> str:
    return (
        "\n\n周期（interval）约定：用户提到「短线、超短、日内短线」等且未明确写出具体 K 线周期（15m/30m/1h/4h/1d）时，"
        f"interval 必须设为 {short_iv}（来自 router policy 常量 ROUTER_POLICY）。"
        "若用户已明确某一合法周期，则以用户为准。"
    )


# 未配置 feishu.llm_router_system_prompt 时使用；以 tools 为主，无 tool_calls 时见 decide_feishu_route 对 assistant 正文的兜底。
DEFAULT_FEISHU_ROUTER_SYSTEM_PROMPT = """你是飞书行情分析机器人的路由器（股票 tickflow、贵金属 goldapi、加密 gateio；可选研报/板块/归属/概念检索；模拟账户余额/持仓/订单/成交查看）。
优先调用提供的工具之一完成意图；不要编造成交、主力资金、交易所逐笔资金流、仓位或「已下单」类结论。
闲聊、致谢或引导用户发起行情分析时，请使用 reply_chat：message 可写多段完整中文，可简要归类列出用户 JSON 里 tradable_assets 相关标的与示例问法（不必抄全表名，分类说明即可）。
若模型接口未返回 tool_calls、仅在 assistant 正文中输出内容，后端也会把正文交给用户；但仍应优先用 reply_chat(message=...) 一次性给出可读回复。
用户消息 JSON 顶层字段：user_message（最新一句）、conversation_transcript（最近若干轮 user/assistant 文本）、
conversation_context（结构化线索：last_task_type、last_symbols 等，非价格事实源）、
policy_injection（内含 tradable_assets、default_symbol、default_interval、short_term_interval_default）。
行情分析必须调用 analyze_market：symbols 只能从 policy_injection.tradable_assets 里的 symbol 选取；单标的也必须传长度为 1 的 symbols 列表，不要传 symbol 单数字段。
如用户问题涉及"查研报""查板块""查归属""查概念""查主题"或类似表达，优先调用 search_research 或 query_concept_board 工具（如仅有关键词可只填 keyword，若有 symbol 可一并填写）。
如用户问题涉及"余额""资金""账户""持仓""订单""成交""仓位""模拟账户"或类似表达，优先调用 view_sim_account 工具。scope 默认 overview（综合），用户明确只问持仓/订单/成交等时可指定 scope。
如用户问题同时涉及行情分析与研报/板块/归属检索，需分别调用 analyze_market 与 search_research/query_concept_board，并分栏输出。
不得将研报检索或板块归属混入行情分析主流程。
interval 仅 15m/30m/1h/4h/1d；用户说短线且未写具体周期时用 short_term_interval_default。
provider 须与所选标的在 tradable_assets 中的 provider 一致。
with_research：用户明确要看研报/机构观点时为 true。
信息不足无法选合法标的或周期时，请调用 reply_chat 自然地反问用户。"""


def _feishu_router_tool_definitions() -> list[dict[str, Any]]:
    """OpenAI-compatible tool list for chat/completions."""
    return [
        {
            "type": "function",
            "function": {
                "name": "analyze_market",
                "description": "用户要行情分析：拉 K 线并生成技术结论。标的必须来自 tradable_assets。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "兼容旧字段：单标的代码，如 BTC_USDT、NVDA；内部会被折叠为 symbols=[symbol]，新输出应优先使用 symbols",
                        },
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "标的代码列表；单标的也必须传单元素列表，如 [\"BTC_USDT\"]",
                        },
                        "interval": {
                            "type": "string",
                            "description": "K 线周期：15m、30m、1h、4h、1d",
                        },
                        "provider": {
                            "type": "string",
                            "description": "tickflow | gateio | goldapi；须与标的表中一致，可省略由后端推断",
                        },
                        "question": {"type": "string", "description": "用户想问的简短中文"},
                        "with_research": {"type": "boolean", "description": "是否附带研报检索"},
                        "research_keyword": {
                            "type": "string",
                            "description": "研报检索关键词，可选",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_research",
                "description": "研报/机构观点/主题/概念/板块检索，支持关键词和可选 symbol。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {
                            "type": "string",
                            "description": "检索关键词，如行业、主题、概念、板块等，可为空（如只查 symbol）",
                        },
                        "symbol": {
                            "type": "string",
                            "description": "可选，标的代码，如 BTC_USDT、NVDA",
                        },
                        "provider": {
                            "type": "string",
                            "description": "可选，数据源，如 yanbaoke、tickflow、gateio、goldapi",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_concept_board",
                "description": "查询标的所属概念/板块归属，支持 symbol 或关键词。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "可选，标的代码，如 BTC_USDT、NVDA",
                        },
                        "keyword": {
                            "type": "string",
                            "description": "可选，概念/板块/主题关键词",
                        },
                        "provider": {
                            "type": "string",
                            "description": "可选，数据源，如 yanbaoke、market_data 等",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "reply_chat",
                "description": "寒暄、致谢、引导用户发起行情分析；用 message 写完整可读回复（可多段，可概括 tradable_assets 中的标的分类与示例问法）。回答尽量简短时不强行压缩：首访寒暄可把支持的资产类型与示例一句话列清。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "回复正文"},
                    },
                    "required": ["message"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "view_sim_account",
                "description": "查看模拟账户状态：余额、持仓、挂单、成交、活动想法、对账统计。用户问「余额/资金/账户/持仓/订单/成交/仓位/模拟账户」时使用。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "description": "查询范围：overview（综合）、positions（持仓）、active_ideas（活动想法）、orders（委托）、fills（成交）、health（对账统计）",
                        },
                        "account_id": {
                            "type": "string",
                            "description": "可选，指定账户 ID 如 CNY/USD",
                        },
                        "symbol": {
                            "type": "string",
                            "description": "可选，指定标的代码",
                        },
                    },
                    "required": [],
                },
            },
        },
    ]


def _parse_tool_arguments(raw: str) -> dict[str, Any]:
    if not (raw or "").strip():
        return {}
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _dedupe_str_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _single_tool_call_to_routed_dict(tool_call: Any) -> dict[str, Any]:
    if not isinstance(tool_call, dict):
        raise LLMClientError("路由 tool_call 结构异常")
    fn = tool_call.get("function")
    if not isinstance(fn, dict):
        raise LLMClientError("路由 tool_call.function 缺失")
    name = str(fn.get("name") or "").strip()
    raw_args = str(fn.get("arguments") or "")
    args = _parse_tool_arguments(raw_args)

    if name == "analyze_market":
        sym = args.get("symbol")
        syms = args.get("symbols")
        out: dict[str, Any] = {"action": "analyze"}
        normalized_symbols: list[str] = []
        if isinstance(syms, list):
            normalized_symbols.extend(str(x).strip() for x in syms if str(x).strip())
        if isinstance(sym, str) and sym.strip():
            normalized_symbols.append(sym.strip())
        if normalized_symbols:
            out["symbols"] = _dedupe_str_list(normalized_symbols)
        iv = str(args.get("interval") or "").strip().lower()
        if iv:
            out["interval"] = iv
        pv = str(args.get("provider") or "").strip().lower()
        if pv:
            out["provider"] = pv
        q = args.get("question")
        if isinstance(q, str) and q.strip():
            out["question"] = q.strip()
        if "with_research" in args:
            out["with_research"] = bool(args.get("with_research"))
        rk = args.get("research_keyword")
        if isinstance(rk, str) and rk.strip():
            out["research_keyword"] = rk.strip()
        return out

    if name == "search_research":
        out: dict[str, Any] = {"action": "research"}
        kw = args.get("keyword")
        sym = args.get("symbol")
        pv = args.get("provider")
        if isinstance(kw, str) and kw.strip():
            out["keyword"] = kw.strip()
        if isinstance(sym, str) and sym.strip():
            out["symbol"] = sym.strip()
        if isinstance(pv, str) and pv.strip():
            out["provider"] = pv.strip()
        return out

    if name == "query_concept_board":
        out: dict[str, Any] = {"action": "concept_board"}
        sym = args.get("symbol")
        kw = args.get("keyword")
        pv = args.get("provider")
        if isinstance(sym, str) and sym.strip():
            out["symbol"] = sym.strip()
        if isinstance(kw, str) and kw.strip():
            out["keyword"] = kw.strip()
        if isinstance(pv, str) and pv.strip():
            out["provider"] = pv.strip()
        return out

    if name == "reply_chat":
        msg = args.get("message")
        if isinstance(msg, str) and msg.strip():
            return {"action": "chat", "chat_reply": msg.strip()}
        return {"action": "chat"}

    if name == "view_sim_account":
        out: dict[str, Any] = {"action": "sim_account"}
        scope = args.get("scope")
        if isinstance(scope, str) and scope.strip():
            out["scope"] = scope.strip()
        aid = args.get("account_id")
        if isinstance(aid, str) and aid.strip():
            out["account_id"] = aid.strip()
        sym = args.get("symbol")
        if isinstance(sym, str) and sym.strip():
            out["symbol"] = sym.strip()
        return out

    raise LLMClientError(f"未知路由工具: {name!r}")


def _merge_tool_routes(routes: list[dict[str, Any]]) -> dict[str, Any]:
    if not routes:
        raise LLMClientError("路由 tool_calls 为空")
    if len(routes) == 1:
        return routes[0]

    actionable = [dict(r) for r in routes if str(r.get("action") or "") != "chat"]
    if not actionable:
        return routes[0]

    analyze_steps = [dict(r) for r in actionable if str(r.get("action") or "") == "analyze"]
    research_steps = [
        dict(r) for r in actionable
        if str(r.get("action") or "") in {"research", "concept_board"}
    ]

    if analyze_steps:
        merged = dict(analyze_steps[0])

        merged_symbols: list[str] = []
        for step in analyze_steps:
            symbols = step.get("symbols")
            if isinstance(symbols, list):
                merged_symbols.extend(str(item).strip() for item in symbols if str(item).strip())
        if merged_symbols:
            merged["symbols"] = _dedupe_str_list(merged_symbols)

        if research_steps or any(bool(step.get("with_research")) for step in analyze_steps):
            merged["with_research"] = True

        if not str(merged.get("research_keyword") or "").strip():
            candidate_keywords: list[str] = []
            for step in research_steps + analyze_steps:
                for key in ("keyword", "research_keyword", "symbol"):
                    value = str(step.get(key) or "").strip()
                    if value:
                        candidate_keywords.append(value)
                        break
            if candidate_keywords:
                merged["research_keyword"] = candidate_keywords[0]

        merged["plan_steps"] = [dict(step) for step in routes]
        return merged

    primary = dict(actionable[0])
    primary["plan_steps"] = [dict(step) for step in routes]
    return primary


def _tool_calls_to_routed_dict(tool_calls: Any) -> dict[str, Any]:
    """将 tool_calls 转为 route_user_message 所需的 dict（必要时做兼容性合并）。"""
    if not isinstance(tool_calls, list) or not tool_calls:
        raise LLMClientError("路由 tool_calls 为空")
    routes = [_single_tool_call_to_routed_dict(tc) for tc in tool_calls]
    return _merge_tool_routes(routes)


def _extract_router_assistant_text(message: dict[str, Any]) -> str:
    """从 chat/completions 的 assistant message 取出可读正文（兼容字符串或多段 content）。"""
    if not isinstance(message, dict):
        return ""
    c = message.get("content")
    if isinstance(c, str):
        return c.strip()
    if isinstance(c, list):
        parts: list[str] = []
        for p in c:
            if isinstance(p, dict):
                if p.get("type") == "text" and isinstance(p.get("text"), str):
                    parts.append(p["text"])
                elif isinstance(p.get("content"), str):
                    parts.append(p["content"])
            elif isinstance(p, str):
                parts.append(p)
        return "".join(parts).strip()
    return ""


def _post_json(url: str, payload: dict[str, Any], timeout_sec: float = 30.0) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(
        url,
        method="POST",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_api_key()}",
        },
    )
    try:
        with urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        try:
            err_body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        snippet = (err_body or str(exc.reason or "")).strip()
        raise LLMClientError(f"LLM HTTP {exc.code}: {snippet[:2000]}") from exc
    except URLError as exc:
        raise LLMClientError(f"LLM 网络请求失败: {exc}") from exc
    except Exception as exc:
        raise LLMClientError(f"LLM 请求失败: {exc}") from exc
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LLMClientError(f"LLM 返回非 JSON: {raw[:240]!r}") from exc
    if isinstance(obj, dict) and obj.get("error"):
        raise LLMClientError(f"LLM 返回错误: {obj.get('error')}")
    return obj if isinstance(obj, dict) else {"raw": obj}


def _build_feishu_route_payload(
    *,
    text: str,
    default_symbol: str,
    default_interval: str,
    recent_messages: list[dict[str, str]] | None,
    tradable_assets: list[dict[str, Any]] | None,
    conversation_context: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    prompt_cfg = _feishu_router_prompt_cfg()
    system_prompt = str(prompt_cfg.get("llm_router_system_prompt") or "").strip()
    if not system_prompt:
        system_prompt = DEFAULT_FEISHU_ROUTER_SYSTEM_PROMPT
    temperature = _resolved_temperature(float(prompt_cfg.get("llm_router_temperature") or 0.0))
    short_iv = _feishu_short_term_interval()
    transcript = list(recent_messages or [])[-20:]
    prompt_obj: dict[str, Any] = {
        "user_message": text or "",
        "conversation_transcript": transcript,
        "conversation_context": dict(conversation_context or {}),
        "policy_injection": {
            "default_symbol": default_symbol,
            "default_interval": default_interval,
            "short_term_interval_default": short_iv,
            "tradable_assets": list(tradable_assets or []),
        },
    }
    url = f"{_base_url()}/chat/completions"
    system_with_hint = system_prompt + _feishu_router_interval_instruction(short_iv=short_iv)
    base_payload: dict[str, Any] = {
        "model": _model_name(),
        "thinking": {"type": "disabled"},
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_with_hint},
            {"role": "user", "content": json.dumps(prompt_obj, ensure_ascii=False)},
        ],
        "tools": _feishu_router_tool_definitions(),
        "tool_choice": "auto",
    }
    return url, base_payload


def _feishu_completion_response_to_route(res: dict[str, Any]) -> dict[str, Any]:
    try:
        msg = res["choices"][0]["message"]
    except Exception as exc:
        raise LLMClientError(f"LLM 路由(tool)响应结构异常: {res}") from exc
    if not isinstance(msg, dict):
        raise LLMClientError(f"LLM 路由 message 非对象: {msg!r}")
    tool_calls = msg.get("tool_calls")
    if tool_calls:
        return _tool_calls_to_routed_dict(tool_calls)
    raw_text = _extract_router_assistant_text(msg)
    if raw_text:
        return {"action": "chat", "chat_reply": raw_text}
    raise LLMClientError("LLM 路由未返回 tool_calls，且无 assistant 正文")


def decide_feishu_route(
    *,
    text: str,
    default_symbol: str,
    default_interval: str,
    recent_messages: list[dict[str, str]] | None = None,
    tradable_assets: list[dict[str, Any]] | None = None,
    conversation_context: dict[str, Any] | None = None,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    """飞书路由：OpenAI-compatible chat/completions + tools；优先 tool_calls；无 tool_calls 时若有 assistant 正文则视为闲聊（action=chat）。

    注：实际调用 provider 由 runtime config 的 llm.default_provider 决定。
    """
    url, payload = _build_feishu_route_payload(
        text=text,
        default_symbol=default_symbol,
        default_interval=default_interval,
        recent_messages=recent_messages,
        tradable_assets=tradable_assets,
        conversation_context=conversation_context,
    )
    res = _post_json(url, payload, timeout_sec=timeout_sec)
    return _feishu_completion_response_to_route(res)


def feishu_route_deepseek_raw_and_routed(
    *,
    text: str,
    default_symbol: str,
    default_interval: str,
    recent_messages: list[dict[str, str]] | None = None,
    tradable_assets: list[dict[str, Any]] | None = None,
    timeout_sec: float = 30.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """真实调用 LLM：返回 (chat/completions 完整 JSON, 路由解析 dict)。供调试脚本打印原始响应。

    注：函数名保留 deepseek 是历史兼容，实际调用 provider 由 runtime config 决定。
    """
    url, payload = _build_feishu_route_payload(
        text=text,
        default_symbol=default_symbol,
        default_interval=default_interval,
        recent_messages=recent_messages,
        tradable_assets=tradable_assets,
        conversation_context=None,
    )
    res = _post_json(url, payload, timeout_sec=timeout_sec)
    return res, _feishu_completion_response_to_route(res)


def generate_decision(
    *,
    symbol: str,
    interval: str,
    question: str | None,
    technical_snapshot: dict[str, Any],
    evidence_sources: list[dict[str, Any]],
    temperature: float = 0.2,
) -> dict[str, Any]:
    bj = now_beijing_str()
    review_example = default_review_time_for_interval(interval)
    prompt_obj = {
        "symbol": symbol,
        "interval": interval,
        "question": question or "",
        "current_time_beijing": bj,
        "technical_snapshot": technical_snapshot,
        "evidence_sources": evidence_sources[:8],
        "constraints": [
            "只依据提供的技术快照与证据，不编造成交、资金流、未提供的价格。",
            "输出必须是 JSON 对象。",
            "必须输出字段: 综合倾向,关键位(Fib),触发条件,失效条件,风险点,下次复核时间。",
            "风险点必须是数组；其余字段用简洁中文。",
            f"当前北京时间（UTC+8）为 {bj}；字段「下次复核时间」必须写具体日期与时刻（北京时间 UTC+8），"
            f"格式与本轮 interval 对齐，示例：{review_example}。禁止仅用「下一根收盘后」等无时间点的模糊句。",
        ],
    }
    base_payload: dict[str, Any] = {
        "model": _model_name(),
        "thinking": {"type": "disabled"},
        "temperature": _resolved_temperature(float(temperature)),
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是交易分析Agent。你只能基于输入证据给出技术结论，"
                    "禁止杜撰成交、主力资金或官方未提供数据。"
                    + _JSON_OBJECT_SYSTEM_SUFFIX
                ),
            },
            {"role": "user", "content": json.dumps(prompt_obj, ensure_ascii=False)},
        ],
    }
    url = f"{_base_url()}/chat/completions"
    try:
        res = _post_json(url, {**base_payload, "response_format": {"type": "json_object"}}, timeout_sec=120.0)
    except LLMClientError as err:
        if "HTTP 400" in str(err):
            res = _post_json(url, base_payload, timeout_sec=120.0)
        else:
            raise
    try:
        content = res["choices"][0]["message"]["content"]
    except Exception as exc:
        raise LLMClientError(f"LLM 响应结构异常: {res}") from exc
    if not isinstance(content, str):
        raise LLMClientError(f"LLM content 非字符串: {content!r}")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMClientError(f"LLM content 不是 JSON: {content[:240]!r}") from exc
    if not isinstance(parsed, dict):
        raise LLMClientError(f"LLM content JSON 非对象: {parsed!r}")
    return parsed


# 飞书：将已锁事实 JSON 写作口语化正文（不得 invent 价位；与 guardrails 禁止口径一致）
DEFAULT_FEISHU_NARRATIVE_SYSTEM = (
    "你是面向飞书聊天的行情分析撰稿人。用户消息为 JSON，其中 facts 为程序算好的事实快照（含 fixed_template、均线、威科夫等）。\n"
    "要求：\n"
    "1) 只使用 facts 中已出现的数字、区间与条件；禁止编造未在 facts 中出现的具体价格、成交量、成交状态或「已可下单」类结论。\n"
    "2) 语气自然、分段清晰，可用少量小标题；避免整段【结论】、━━ 等刻板公文排版。\n"
    "3) 禁止出现以下口径：已成交、成交回报、主力资金净流入、交易所逐笔资金流。\n"
    "4) 文末用一句话声明：仅供技术分析与程序化演示，不构成投资建议。\n"
    "输出为纯中文正文，不要使用 Markdown 代码围栏。"
)


def generate_feishu_narrative(
    *,
    facts: dict[str, Any],
    user_question: str | None = None,
    timeout_sec: float = 120.0,
) -> str:
    """基于工具锁事实生成飞书可读长文；不负责拉行情。"""
    cfg = get_analysis_config()
    fei = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    temperature = _resolved_temperature(float(fei.get("narrative_temperature", 0.35)))
    custom = str(fei.get("narrative_system_prompt") or "").strip()
    system_prompt = custom if custom else DEFAULT_FEISHU_NARRATIVE_SYSTEM
    user_obj: dict[str, Any] = {"facts": facts}
    if user_question and str(user_question).strip():
        user_obj["user_question"] = str(user_question).strip()
    url = f"{_base_url()}/chat/completions"
    payload: dict[str, Any] = {
        "model": _model_name(),
        "thinking": {"type": "disabled"},
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_obj, ensure_ascii=False)},
        ],
    }
    res = _post_json(url, payload, timeout_sec=timeout_sec)
    try:
        content = res["choices"][0]["message"]["content"]
    except Exception as exc:
        raise LLMClientError(f"LLM 叙事响应结构异常: {res}") from exc
    if not isinstance(content, str):
        raise LLMClientError(f"LLM 叙事 content 非字符串: {content!r}")
    text = content.strip()
    if not text:
        raise LLMClientError("LLM 叙事返回空正文")
    return text


def _format_display_preferences_hint(dp: dict[str, Any]) -> str:
    parts: list[str] = []
    p = dp.get("precision")
    if isinstance(p, int) and p >= 0:
        parts.append(f"数值展示：金额与数量统一保留 {p} 位小数（四舍五入），以可读为先。")
    if dp.get("compact"):
        parts.append("篇幅：尽量简短，列表式要点即可。")
    if dp.get("detailed"):
        parts.append("篇幅：可适当展开说明，仍不得编造未提供的数据。")
    if dp.get("repeat"):
        parts.append("用户要求重复上一轮要点：在事实不变前提下简要复述。")
    return "\n".join(parts)


GROUNDED_WRITER_SYSTEM_BY_MODE: dict[str, str] = {
    "quick": (
        "你是金融简报撰稿人。用户 JSON 内含 task_type、response_mode 与 facts_bundle。\n"
        "要求：\n"
        "1) 只引用 facts_bundle 中出现的数值与中文描述；禁止编造价格、成交或资金流。\n"
        "2) 回答要短（现价类几句话即可），禁止输出代码字段名或英文键名（如 triggered、preferred_side、entry=None、aligned）。\n"
        "3) 禁止口径：已成交、成交回报、主力资金净流入、交易所逐笔资金流。\n"
        "4) 文末一句：仅供技术分析与程序化演示，不构成投资建议。\n"
        "输出纯中文正文，无 Markdown 代码围栏。"
    ),
    "compare": (
        "你是多资产对比撰稿人。依据 facts_bundle 中的多标的事实（含 compare_facts.rows）做排序或强弱判断说明。\n"
        "只使用已给出的价格、趋势、共振等字段；禁止编造；禁止输出编程字段名；禁止具体下单指令。\n"
        "文末免责声明：仅供技术分析与程序化演示，不构成投资建议。\n"
        "输出纯中文正文。"
    ),
    "analysis": (
        "你是行情分析撰稿人。facts_bundle.market_facts.analysis_facts 为程序算好的技术快照（含 fixed_template、均线、威科夫摘要）。\n"
        "自然分段，避免「━━」「【结论】」式刻板排版；不得编造未出现的数据；禁止将编程字段名原样输出给用户。\n"
        "文末免责声明：仅供技术分析与程序化演示，不构成投资建议。\n"
        "输出纯中文正文。"
    ),
    "narrative": (
        "你是研报线索撰稿人。facts_bundle.research_facts 为检索摘要，不得写成「已验证价格触发」或交易指令。\n"
        "不写具体 entry/stop/tp；不编造机构已确认成交；可列观点分歧与需二次验证之处。\n"
        "文末免责声明：仅供技术分析与程序化演示，不构成投资建议。\n"
        "输出纯中文正文。"
    ),
    "sim_account": (
        "你是模拟账户数据播报员。facts_bundle.sim_account_facts 为程序查询结果（含 metrics/tables/summary），"
        "只使用其中已出现的数字与账户字段；禁止编造成交回报、主力资金、交易所逐笔资金流。\n"
        "将余额、持仓、委托、成交等信息用自然中文分段说明；勿输出英文键名或 JSON。\n"
        "若用户要求小数位数或简短/详细，严格按 user JSON 中的 display_preferences 执行。\n"
        "文末免责声明：仅供技术分析与程序化演示，不构成投资建议。\n"
        "输出纯中文正文。"
    ),
}


def generate_grounded_answer(
    *,
    facts_bundle: dict[str, Any],
    user_question: str | None,
    task_type: str,
    response_mode: str,
    display_preferences: dict[str, Any] | None = None,
    timeout_sec: float = 120.0,
) -> dict[str, Any]:
    """基于 facts_bundle 的 grounded 撰稿；返回 text/sections/style。"""
    cfg = get_analysis_config()
    agent = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    fei = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    temperature = _resolved_temperature(float(agent.get("writer_temperature", fei.get("narrative_temperature", 0.35))))
    custom = str(agent.get("writer_system_prompt") or "").strip()
    mode_key = str(response_mode or "analysis").strip().lower()
    if mode_key not in GROUNDED_WRITER_SYSTEM_BY_MODE:
        if str(task_type or "").strip().lower() == "sim_account":
            mode_key = "sim_account"
        else:
            mode_key = "analysis"
    system_prompt = custom if custom else GROUNDED_WRITER_SYSTEM_BY_MODE[mode_key]
    model = str(agent.get("writer_model") or "").strip() or _model_name()
    user_obj: dict[str, Any] = {
        "task_type": task_type,
        "response_mode": response_mode,
        "facts_bundle": facts_bundle,
    }
    if display_preferences and isinstance(display_preferences, dict) and display_preferences:
        user_obj["display_preferences"] = display_preferences
        dp_hint = _format_display_preferences_hint(display_preferences)
        if dp_hint:
            system_prompt = system_prompt + "\n\n" + dp_hint
    if user_question and str(user_question).strip():
        user_obj["user_question"] = str(user_question).strip()
    url = f"{_base_url()}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "thinking": {"type": "disabled"},
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_obj, ensure_ascii=False)},
        ],
    }
    res = _post_json(url, payload, timeout_sec=timeout_sec)
    try:
        content = res["choices"][0]["message"]["content"]
    except Exception as exc:
        raise LLMClientError(f"LLM grounded 响应结构异常: {res}") from exc
    if not isinstance(content, str):
        raise LLMClientError(f"LLM grounded content 非字符串: {content!r}")
    text = content.strip()
    if not text:
        raise LLMClientError("LLM grounded 返回空正文")
    return {"text": text, "sections": [{"title": "正文", "content": text}], "style": response_mode}