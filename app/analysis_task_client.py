"""HTTP 异步分析任务客户端（Agent Core / Facade 共用）。"""
from __future__ import annotations

import time
from typing import Any

import requests


def submit_analysis_task(*, api_base_url: str, payload: dict[str, Any], timeout_sec: float = 20.0) -> str:
    url = f"{api_base_url.rstrip('/')}/agent/analyze"
    resp = requests.post(url, json=payload, timeout=timeout_sec)
    resp.raise_for_status()
    obj = resp.json()
    task_id = str(obj.get("task_id") or "").strip()
    if not task_id:
        raise RuntimeError(f"提交分析任务失败: {obj}")
    return task_id


def poll_analysis_result(
    *,
    api_base_url: str,
    task_id: str,
    timeout_sec: float = 120.0,
    poll_interval_sec: float = 2.0,
) -> dict[str, Any]:
    url = f"{api_base_url.rstrip('/')}/agent/tasks/{task_id}"
    start = time.time()
    while True:
        resp = requests.get(url, timeout=20.0)
        resp.raise_for_status()
        obj = resp.json()
        status = str(obj.get("status") or "")
        if status == "completed":
            result = obj.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"任务完成但 result 非对象: {obj}")
            return result
        if status == "failed":
            raise RuntimeError(f"分析任务失败: {obj.get('error')}")
        if time.time() - start > timeout_sec:
            raise TimeoutError(f"轮询分析任务超时: {task_id}")
        time.sleep(max(0.5, poll_interval_sec))
