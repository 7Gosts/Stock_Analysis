#!/usr/bin/env python3
"""最小化 DeepSeek 连通性检测：不启动飞书/API，只打一条 chat/completions。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.deepseek.client import (  # noqa: E402
    DeepSeekError,
    _DEEPSEEK_JSON_OBJECT_SYSTEM_SUFFIX,
    _base_url,
    _model_name,
    _post_json,
)


def _mask_key_hint() -> str:
    from tools.deepseek.client import _api_key

    try:
        k = _api_key()
    except DeepSeekError:
        return "(无 Key)"
    if len(k) < 12:
        return "(已配置)"
    return f"{k[:4]}…{k[-4:]}"


def main() -> int:
    url = f"{_base_url()}/chat/completions"
    model = _model_name()
    print(f"[deepseek_ping] base_url={_base_url()}")
    print(f"[deepseek_ping] model={model}")
    print(f"[deepseek_ping] api_key={_mask_key_hint()}")

    minimal = {
        "model": model,
        "temperature": 0,
        "max_tokens": 16,
        "messages": [{"role": "user", "content": "Reply only: OK"}],
    }

    print("\n--- Test A: minimal chat (no response_format) ---")
    try:
        res = _post_json(url, minimal, timeout_sec=20.0)
        msg = res.get("choices", [{}])[0].get("message", {}).get("content", "")
        print(f"OK choices[0].content={msg!r}")
    except DeepSeekError as e:
        print(f"FAIL {e}")

    print("\n--- Test B: chat + response_format json_object (system 须含字面 json，与路由一致) ---")
    payload_b = {
        "model": model,
        "temperature": 0,
        "max_tokens": 64,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "Reply with one JSON object only." + _DEEPSEEK_JSON_OBJECT_SYSTEM_SUFFIX,
            },
            {"role": "user", "content": '{"task":"ping"}'},
        ],
    }
    try:
        res = _post_json(url, payload_b, timeout_sec=20.0)
        msg = res.get("choices", [{}])[0].get("message", {}).get("content", "")
        print(f"OK choices[0].content={msg!r}")
        try:
            json.loads(msg)
            print("OK content parses as JSON")
        except json.JSONDecodeError:
            print("WARN content is not valid JSON string")
    except DeepSeekError as e:
        print(f"FAIL {e}")

    print("\n说明：若此前飞书路由报 HTTP 400 + Prompt must contain the word json，根因是 DeepSeek 要求")
    print("      在启用 response_format=json_object 时，messages 文本里必须出现子串 json（与 HTTP 体字段无关）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
