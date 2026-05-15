"""会话状态层：结构化上下文存储（三层重构核心模块）。

职责：保存本轮和上一轮的结构化上下文，不保存完整事实正文。
只回答一个问题：用户此刻说的"它 / 这个 / xx / 这个入场"指的是哪一轮分析对象？

关键改变：
1. 错误响应优先视为 chat-style fallback + structured meta。
2. 新增容错闭环字段：route_attempts, last_error_code, repair_history, termination_reason。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass
class SessionState:
    """结构化会话状态。

    核心字段：
    - last_action: 最近一次路由 action（analyze / chat / quote / compare / research / followup）
    - last_task_type: 最近一次任务类型（analysis / quote / compare / research / followup / chat）
    - last_symbols: 最近一次分析标的列表（推荐使用，单标的也用列表）
    - last_interval / last_provider / last_question: 辅助上下文

    容错闭环字段：
    - route_attempts: 当前请求路由尝试次数（每轮请求开始时重置）
    - last_error_code: 最近一次错误码（如 route_missing_symbols）
    - repair_history: 修正历史记录（本轮请求的所有尝试）
    - termination_reason: 最终终止原因（success / error_code / max_attempts_reached）

    兼容字段：
    - last_symbol: 单标的兼容，推荐使用 last_symbols
    """
    open_id: str
    last_action: str = "chat"  # analyze / chat / quote / compare / research / followup
    last_task_type: str = "chat"  # analysis / quote / compare / research / followup / chat
    last_symbol: str | None = None  # 兼容单标的场景（推荐使用 last_symbols）
    last_symbols: list[str] = field(default_factory=list)  # 多标的场景（统一协议）
    last_interval: str | None = None
    last_provider: str | None = None
    last_question: str | None = None
    last_output_refs: dict[str, str] = field(default_factory=dict)  # ai_overview_path, full_report_path 等

    # 聊天 Agent：上一轮事实快照（供展示修正复用，非交易所成交事实源）
    last_facts_bundle: dict[str, Any] = field(default_factory=dict)
    last_display_preferences: dict[str, Any] = field(default_factory=dict)
    last_sim_account_scope: str | None = None
    history_version: int = 0
    compacted_summary: str | None = None

    updated_ts: float = field(default_factory=time.time)

    # 容错闭环字段
    route_attempts: int = 0  # 当前请求路由尝试次数（每轮请求开始时重置为 0）
    last_error_code: str | None = None  # 最近一次错误码（如 route_missing_symbols, analysis_backend_unavailable）
    repair_history: list[dict[str, Any]] = field(default_factory=list)  # 修正历史（本轮所有尝试）
    termination_reason: str | None = None  # 最终终止原因（success / error_code / max_attempts_reached）

    def to_dict(self) -> dict[str, Any]:
        return {
            "open_id": self.open_id,
            "last_action": self.last_action,
            "last_task_type": self.last_task_type,
            "last_symbol": self.last_symbol,
            "last_symbols": self.last_symbols,
            "last_interval": self.last_interval,
            "last_provider": self.last_provider,
            "last_question": self.last_question,
            "last_output_refs": self.last_output_refs,
            "last_facts_bundle": self.last_facts_bundle,
            "last_display_preferences": self.last_display_preferences,
            "last_sim_account_scope": self.last_sim_account_scope,
            "history_version": self.history_version,
            "compacted_summary": self.compacted_summary,
            "updated_ts": self.updated_ts,
            # 新增字段
            "route_attempts": self.route_attempts,
            "last_error_code": self.last_error_code,
            "repair_history": self.repair_history,
            "termination_reason": self.termination_reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SessionState":
        return cls(
            open_id=str(d.get("open_id") or ""),
            last_action=str(d.get("last_action") or "chat"),
            last_task_type=str(d.get("last_task_type") or "chat"),
            last_symbol=d.get("last_symbol"),
            last_symbols=list(d.get("last_symbols") or []),
            last_interval=d.get("last_interval"),
            last_provider=d.get("last_provider"),
            last_question=d.get("last_question"),
            last_output_refs=dict(d.get("last_output_refs") or {}),
            last_facts_bundle=dict(d.get("last_facts_bundle") or {}),
            last_display_preferences=dict(d.get("last_display_preferences") or {}),
            last_sim_account_scope=d.get("last_sim_account_scope"),
            history_version=int(d.get("history_version") or 0),
            compacted_summary=d.get("compacted_summary"),
            updated_ts=float(d.get("updated_ts") or time.time()),
            # 新增字段
            route_attempts=int(d.get("route_attempts") or 0),
            last_error_code=d.get("last_error_code"),
            repair_history=list(d.get("repair_history") or []),
            termination_reason=d.get("termination_reason"),
        )


class SessionStateStore:
    """会话状态存储：内存 + 可选持久化。

    与飞书历史层完全分离，只存储结构化状态，不存储聊天文本。
    """
    _DEFAULT_TTL_SEC = 30 * 60  # 30 分钟

    def __init__(
        self,
        *,
        persist_path: Path | None = None,
        ttl_sec: int = _DEFAULT_TTL_SEC,
    ) -> None:
        self._states: dict[str, SessionState] = {}
        self._lock = threading.Lock()
        self._ttl_sec = max(60, int(ttl_sec))
        self._persist_path = persist_path
        if persist_path:
            persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    def get(self, open_id: str) -> SessionState:
        """获取用户会话状态，不存在则返回空状态。"""
        key = str(open_id or "").strip()
        if not key:
            return SessionState(open_id="")
        now = time.time()
        with self._lock:
            self._expire_old_states(now)
            st = self._states.get(key)
            if st is None:
                st = SessionState(open_id=key)
                self._states[key] = st
            return st

    def update(self, state: SessionState) -> None:
        """更新会话状态并持久化。"""
        key = str(state.open_id or "").strip()
        if not key:
            return
        state.updated_ts = time.time()
        with self._lock:
            self._states[key] = state
        if self._persist_path:
            self._save_to_disk()

    def update_from_route(
        self,
        open_id: str,
        *,
        action: str,
        task_type: str,
        symbol: str | None = None,
        symbols: list[str] | None = None,
        interval: str | None = None,
        provider: str | None = None,
        question: str | None = None,
        output_refs: dict[str, str] | None = None,
    ) -> SessionState:
        """从路由结果更新状态（统一入口）。

        注意：
        - symbols 是统一协议，单标的也用长度为 1 的列表。

        Args:
            open_id: 用户标识
            action: 路由 action（analyze / chat / quote / compare / research / followup）
            task_type: 任务类型（analysis / quote / compare / research / followup / chat）
            symbol: 单标的（兼容，推荐使用 symbols）
            symbols: 标的列表（统一协议）
            interval: 周期
            provider: 数据源
            question: 用户问题
            output_refs: 本轮产物路径（供下一轮追问使用）

        Returns:
            更新后的 SessionState
        """
        st = self.get(open_id)
        st.last_action = str(action or "chat").strip().lower()
        st.last_task_type = str(task_type or "chat").strip().lower()
        if symbol:
            st.last_symbol = str(symbol).strip().upper()
        if symbols:
            st.last_symbols = [str(s).strip().upper() for s in symbols if s]
        if interval:
            st.last_interval = str(interval).strip().lower()
        if provider:
            st.last_provider = str(provider).strip().lower()
        if question:
            st.last_question = str(question).strip()
        if output_refs:
            st.last_output_refs = dict(output_refs)
        self.update(st)
        return st

    def record_error(
        self,
        open_id: str,
        *,
        error_code: str,
        error_stage: str = "unknown",
        error_message: str | None = None,
        recoverable: bool = False,
    ) -> SessionState:
        """记录错误信息（用于后续 repair loop）。

        Args:
            open_id: 用户标识
            error_code: 错误码（如 route_missing_symbols）
            error_stage: 错误阶段（route / execute / infra）
            error_message: 错误详情
            recoverable: 是否可恢复

        Returns:
            更新后的 SessionState
        """
        st = self.get(open_id)
        st.last_error_code = str(error_code).strip()
        attempt = st.route_attempts
        if str(error_stage or "unknown").strip().lower() == "route":
            st.route_attempts += 1
            attempt = st.route_attempts

        # 添加到修正历史
        repair_entry = {
            "attempt": attempt,
            "error_code": error_code,
            "error_stage": error_stage,
            "error_message": error_message,
            "recoverable": recoverable,
            "timestamp": time.time(),
        }
        st.repair_history.append(repair_entry)
        self.update(st)
        return st

    def record_success(
        self,
        open_id: str,
        *,
        termination_reason: str = "success",
    ) -> SessionState:
        """记录成功状态。

        Args:
            open_id: 用户标识
            termination_reason: 终止原因（默认 success）

        Returns:
            更新后的 SessionState
        """
        st = self.get(open_id)
        st.termination_reason = str(termination_reason).strip()
        st.last_error_code = None  # 成功时清空错误码
        self.update(st)
        return st

    def record_final_termination(
        self,
        open_id: str,
        *,
        termination_reason: str,
        final_error_code: str | None = None,
    ) -> SessionState:
        """记录最终终止状态（达到最大尝试次数或其他不可恢复情况）。

        Args:
            open_id: 用户标识
            termination_reason: 终止原因（如 max_attempts_reached）
            final_error_code: 最终错误码（可选）

        Returns:
            更新后的 SessionState
        """
        st = self.get(open_id)
        st.termination_reason = str(termination_reason).strip()
        if final_error_code:
            st.last_error_code = str(final_error_code).strip()
        self.update(st)
        return st

    def reset_route_attempts(self, open_id: str) -> SessionState:
        """重置路由尝试次数（新一轮请求开始时调用）。

        Args:
            open_id: 用户标识

        Returns:
            更新后的 SessionState
        """
        st = self.get(open_id)
        st.route_attempts = 0
        st.last_error_code = None
        st.termination_reason = None
        st.repair_history = []  # 清空本轮修正历史
        self.update(st)
        return st

    def resolve_followup_target(self, open_id: str, text: str) -> dict[str, Any]:
        """追问目标解析：返回上一轮分析对象。

        注意：
        - 只检查有效的分析类 action（analyze / research / quote / compare / followup）

        返回：
        - symbol: 上一轮标的
        - interval: 上一轮周期
        - provider: 上一轮 provider
        - output_refs: 上一轮产物路径
        - resolved: 是否成功解析
        """
        st = self.get(open_id)
        # 有效追问来源：analyze / research / quote / compare / followup
        valid_actions = {"analyze", "analysis", "research", "quote", "compare", "followup", "analyze_multi", "sim_account"}
        if st.last_action not in valid_actions:
            return {"resolved": False, "reason": "上一轮非分析任务"}
        if not st.last_symbol and not st.last_symbols:
            return {"resolved": False, "reason": "上一轮无标的"}
        return {
            "resolved": True,
            "symbol": st.last_symbol,
            "symbols": st.last_symbols,
            "interval": st.last_interval,
            "provider": st.last_provider,
            "output_refs": st.last_output_refs,
            "last_action": st.last_action,
            "last_task_type": st.last_task_type,
            "last_question": st.last_question,
        }

    def _expire_old_states(self, now: float) -> None:
        expired = [
            k for k, v in self._states.items()
            if (now - v.updated_ts) > self._ttl_sec
        ]
        for k in expired:
            self._states.pop(k, None)

    def _load_from_disk(self) -> None:
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict):
            return
        with self._lock:
            for k, v in data.items():
                if isinstance(v, dict):
                    self._states[k] = SessionState.from_dict(v)

    def _save_to_disk(self) -> None:
        if not self._persist_path:
            return
        with self._lock:
            data = {k: v.to_dict() for k, v in self._states.items()}
        try:
            self._persist_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass


def _default_persist_path() -> Path | None:
    env = os.getenv("SESSION_STATE_PERSIST_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return None


def get_global_session_store() -> SessionStateStore:
    """全局单例（供飞书服务使用）。"""
    global _GLOBAL_SESSION_STORE
    if _GLOBAL_SESSION_STORE is None:
        _GLOBAL_SESSION_STORE = SessionStateStore(persist_path=_default_persist_path())
    return _GLOBAL_SESSION_STORE


_GLOBAL_SESSION_STORE: SessionStateStore | None = None