from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config.runtime_config import get_analysis_config


class DeepSeekError(RuntimeError):
    pass


# DeepSeek：使用 response_format=json_object 时，messages 全文须出现子串 "json"（见 API 报错 invalid_request_error）
_DEEPSEEK_JSON_OBJECT_SYSTEM_SUFFIX = "\n\n(json: Your entire reply must be one JSON object.)"


def _base_url() -> str:
    env_url = os.getenv("DEEPSEEK_BASE_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")
    cfg = get_analysis_config()
    node = cfg.get("deepseek") if isinstance(cfg.get("deepseek"), dict) else {}
    url = str(node.get("base_url") or "").strip()
    return (url or "https://api.deepseek.com").rstrip("/")


def _model_name() -> str:
    env_model = os.getenv("DEEPSEEK_MODEL", "").strip()
    if env_model:
        return env_model
    cfg = get_analysis_config()
    node = cfg.get("deepseek") if isinstance(cfg.get("deepseek"), dict) else {}
    model = str(node.get("model") or "").strip()
    return model or "deepseek-v4-flash"


def _api_key() -> str:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        cfg = get_analysis_config()
        node = cfg.get("deepseek") if isinstance(cfg.get("deepseek"), dict) else {}
        key = str(node.get("api_key") or "").strip()
    if not key:
        raise DeepSeekError("缺少 DeepSeek API Key（环境变量 DEEPSEEK_API_KEY 或 config/analysis_defaults.yaml）。")
    return key


def _feishu_router_prompt_cfg() -> dict[str, Any]:
    cfg = get_analysis_config()
    node = cfg.get("feishu") if isinstance(cfg.get("feishu"), dict) else {}
    return node if isinstance(node, dict) else {}


def _feishu_short_term_interval() -> str:
    """飞书：用户说「短线」等且未明确周期时使用的默认 interval（配置 feishu.short_term_interval）。"""
    allowed = {"15m", "30m", "1h", "4h", "1d"}
    cfg = _feishu_router_prompt_cfg()
    raw = str(cfg.get("short_term_interval") or "4h").strip().lower()
    return raw if raw in allowed else "4h"


def _feishu_router_interval_instruction(*, short_iv: str) -> str:
    return (
        "\n\n周期（interval）约定：用户提到「短线、超短、日内短线」等且未明确写出具体 K 线周期（15m/30m/1h/4h/1d）时，"
        f"interval 必须设为 {short_iv}（来自配置 feishu.short_term_interval，可在 yaml 修改）。"
        "若用户已明确某一合法周期，则以用户为准。"
    )


# 未配置 llm_router_system_prompt 时使用；路由约束仅在此与 YAML 的 system 字段中维护，不在 Python 里维护 rules 列表。
DEFAULT_ROUTER_SYSTEM_PROMPT = """你是飞书加密货币行情分析机器人的路由器。
你必须只输出一个 JSON 对象（不要 Markdown、不要代码围栏）。

字段（按需填写，未用到的填 null 或空字符串）：
action、symbol、symbols、interval、question、clarify_message、chat_reply。

action 取值：
- analyze：用户要行情分析；symbol / symbols 只能从用户 JSON 里 allowed_gateio_symbols 列表中选（多标的时 symbols 至少 2 项）；口语里的 eth/btc 等须映射成列表中的 *_USDT 对；interval 仅允许 15m/30m/1h/4h/1d；question 用简短中文描述要问什么。若无法选出合法标的，用 action=clarify。
- clarify：标的或周期仍无法从当前句与对话上下文确定；clarify_message 说明缺什么。
- chat：寒暄或非行情分析；chat_reply 直接回复用户。

不要编造成交、主力资金、交易所逐笔资金流、仓位或「已下单」类结论。"""


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
        raise DeepSeekError(f"DeepSeek HTTP {exc.code}: {snippet[:2000]}") from exc
    except URLError as exc:
        raise DeepSeekError(f"DeepSeek 网络请求失败: {exc}") from exc
    except Exception as exc:
        raise DeepSeekError(f"DeepSeek 请求失败: {exc}") from exc
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeepSeekError(f"DeepSeek 返回非 JSON: {raw[:240]!r}") from exc
    if isinstance(obj, dict) and obj.get("error"):
        raise DeepSeekError(f"DeepSeek 返回错误: {obj.get('error')}")
    return obj if isinstance(obj, dict) else {"raw": obj}


def generate_decision(
    *,
    symbol: str,
    interval: str,
    question: str | None,
    technical_snapshot: dict[str, Any],
    evidence_sources: list[dict[str, Any]],
    temperature: float = 0.2,
) -> dict[str, Any]:
    prompt_obj = {
        "symbol": symbol,
        "interval": interval,
        "question": question or "",
        "technical_snapshot": technical_snapshot,
        "evidence_sources": evidence_sources[:8],
        "constraints": [
            "只依据提供的技术快照与证据，不编造成交、资金流、未提供的价格。",
            "输出必须是 JSON 对象。",
            "必须输出字段: 综合倾向,关键位(Fib),触发条件,失效条件,风险点,下次复核时间。",
            "风险点必须是数组；其余字段用简洁中文。",
        ],
    }
    base_payload: dict[str, Any] = {
        "model": _model_name(),
        "temperature": float(temperature),
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是交易分析Agent。你只能基于输入证据给出技术结论，"
                    "禁止杜撰成交、主力资金或官方未提供数据。"
                    + _DEEPSEEK_JSON_OBJECT_SYSTEM_SUFFIX
                ),
            },
            {"role": "user", "content": json.dumps(prompt_obj, ensure_ascii=False)},
        ],
    }
    url = f"{_base_url()}/chat/completions"
    try:
        res = _post_json(url, {**base_payload, "response_format": {"type": "json_object"}})
    except DeepSeekError as err:
        if "HTTP 400" in str(err):
            res = _post_json(url, base_payload)
        else:
            raise
    try:
        content = res["choices"][0]["message"]["content"]
    except Exception as exc:
        raise DeepSeekError(f"DeepSeek 响应结构异常: {res}") from exc
    if not isinstance(content, str):
        raise DeepSeekError(f"DeepSeek content 非字符串: {content!r}")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise DeepSeekError(f"DeepSeek content 不是 JSON: {content[:240]!r}") from exc
    if not isinstance(parsed, dict):
        raise DeepSeekError(f"DeepSeek content JSON 非对象: {parsed!r}")
    return parsed


def decide_message_action(
    *,
    text: str,
    default_symbol: str,
    default_interval: str,
    recent_messages: list[dict[str, str]] | None = None,
    allowed_gateio_symbols: list[str] | None = None,
    timeout_sec: float = 12.0,
) -> dict[str, Any]:
    prompt_cfg = _feishu_router_prompt_cfg()
    system_prompt = str(prompt_cfg.get("llm_router_system_prompt") or "").strip()
    if not system_prompt:
        system_prompt = DEFAULT_ROUTER_SYSTEM_PROMPT
    temperature = float(prompt_cfg.get("llm_router_temperature") or 0.0)

    short_iv = _feishu_short_term_interval()
    prompt_obj = {
        "text": text or "",
        "default_symbol": default_symbol,
        "default_interval": default_interval,
        "recent_messages": recent_messages or [],
        "short_term_interval_default": short_iv,
        "allowed_gateio_symbols": list(allowed_gateio_symbols or []),
    }
    url = f"{_base_url()}/chat/completions"
    system_with_hint = (
        system_prompt
        + _feishu_router_interval_instruction(short_iv=short_iv)
        + _DEEPSEEK_JSON_OBJECT_SYSTEM_SUFFIX
    )
    base_payload: dict[str, Any] = {
        "model": _model_name(),
        "temperature": temperature,
        "messages": [
            {
                "role": "system",
                "content": system_with_hint,
            },
            {"role": "user", "content": json.dumps(prompt_obj, ensure_ascii=False)},
        ],
    }
    try:
        res = _post_json(
            url,
            {**base_payload, "response_format": {"type": "json_object"}},
            timeout_sec=timeout_sec,
        )
    except DeepSeekError as err:
        # 部分模型/套餐不接受 response_format，400 时去掉 JSON 模式重试一次
        err_text = str(err)
        if "HTTP 400" in err_text or "HTTP 400:" in err_text:
            res = _post_json(url, base_payload, timeout_sec=timeout_sec)
        else:
            raise
    try:
        content = res["choices"][0]["message"]["content"]
    except Exception as exc:
        raise DeepSeekError(f"DeepSeek 路由决策响应结构异常: {res}") from exc
    if not isinstance(content, str):
        raise DeepSeekError(f"DeepSeek 路由决策 content 非字符串: {content!r}")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise DeepSeekError(f"DeepSeek 路由决策 content 不是 JSON: {content[:240]!r}") from exc
    if not isinstance(parsed, dict):
        raise DeepSeekError(f"DeepSeek 路由决策结果非对象: {parsed!r}")
    return parsed
