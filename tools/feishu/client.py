from __future__ import annotations

import json
from typing import Any

import requests


class FeishuError(RuntimeError):
    pass


def get_tenant_access_token(*, app_id: str, app_secret: str, timeout_sec: float = 30.0) -> str:
    if not app_id or not app_secret:
        raise FeishuError("缺少飞书 app_id/app_secret。")
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": app_id, "app_secret": app_secret}
    headers = {"Content-Type": "application/json; charset=utf-8"}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout_sec)
        resp.raise_for_status()
    except Exception as exc:
        raise FeishuError(f"获取 tenant_access_token 失败: {exc}") from exc
    try:
        obj = resp.json()
    except Exception as exc:
        raise FeishuError(f"tenant_access_token 响应非 JSON: {resp.text[:240]!r}") from exc
    if int(obj.get("code", 0)) != 0:
        raise FeishuError(f"获取 tenant_access_token 失败: {obj}")
    token = str(obj.get("tenant_access_token") or "").strip()
    if not token:
        raise FeishuError(f"tenant_access_token 为空: {obj}")
    return token


def send_text_message(
    *,
    tenant_access_token: str,
    receive_id: str,
    text: str,
    receive_id_type: str = "open_id",
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    if not tenant_access_token:
        raise FeishuError("缺少 tenant_access_token。")
    if not receive_id:
        raise FeishuError("缺少 receive_id。")
    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    params = {"receive_id_type": receive_id_type}
    headers = {
        "Authorization": f"Bearer {tenant_access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "receive_id": receive_id,
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    try:
        resp = requests.post(url, params=params, headers=headers, json=payload, timeout=timeout_sec)
        resp.raise_for_status()
    except Exception as exc:
        raise FeishuError(f"发送飞书消息失败: {exc}") from exc
    try:
        obj = resp.json()
    except Exception as exc:
        raise FeishuError(f"飞书消息响应非 JSON: {resp.text[:240]!r}") from exc
    if int(obj.get("code", 0)) != 0:
        raise FeishuError(f"发送飞书消息失败: {obj}")
    return obj
