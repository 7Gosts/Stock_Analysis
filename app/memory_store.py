"""飞书历史存储层（三层重构版）。

职责收敛为：
1. 飞书会话历史存储（conversation_memory）
2. 上下文辅助恢复（仅用于指代消解和风格延续）

关键改变：
- 与 session_state.py 完全分离
- 只存储飞书消息文本，不存储结构化状态
- 不作为事实源本体（文档要求：历史消息只用于消歧与语言承接）
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@dataclass
class MemoryEvent:
    """飞书历史事件（只存储消息文本，不存储结构化事实）。"""
    open_id: str
    role: str  # user / assistant
    text: str
    action: str | None = None  # clarify / chat / analyze / followup
    symbol: str | None = None  # 仅用于辅助标注，不作为事实源
    interval: str | None = None
    created_ts: float | None = None

    def to_dict(self) -> dict[str, Any]:
        ts = float(self.created_ts or time.time())
        d: dict[str, Any] = {
            "open_id": self.open_id,
            "role": self.role,
            "text": self.text,
            "action": self.action,
            "created_ts": ts,
        }
        if self.symbol:
            d["symbol"] = self.symbol
        if self.interval:
            d["interval"] = self.interval
        return d


class JsonlMemoryStore:
    """飞书历史存储（JSONL 后端）。

    只用于：
    1. 指代消解（"这个"、"它"等）
    2. 多轮追问承接
    3. 用户语言风格延续
    4. 当本地事实未命中时提供弱补充

    禁止用于：
    1. entry / stop / tp1 / tp2 / triggered 等状态判断
    2. "已入场 / 待触发 / 已止盈 / 已止损"等状态推断
    """

    def __init__(
        self,
        *,
        path: Path,
        max_messages_per_user: int = 2000,
        history_days: int = 30,
    ) -> None:
        self.path = path
        self.max_messages_per_user = max(100, int(max_messages_per_user))
        self.history_days = max(1, int(history_days))
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def append_event(self, event: MemoryEvent) -> None:
        """追加一条历史事件。"""
        row = event.to_dict()
        if not row["open_id"] or not row["role"] or not row["text"]:
            return
        line = json.dumps(row, ensure_ascii=False)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def load_recent(self, *, open_id: str, limit: int = 8) -> list[dict[str, Any]]:
        """加载最近 N 条历史（用于指代消解）。"""
        key = str(open_id or "").strip()
        if not key or limit <= 0:
            return []
        rows = self._read_all()
        out = [r for r in rows if str(r.get("open_id") or "").strip() == key]
        out.sort(key=lambda x: float(x.get("created_ts") or 0.0))
        return out[-int(limit):]

    def load_last_profile(self, *, open_id: str) -> dict[str, str]:
        """加载最近一次分析任务的辅助标注（仅用于默认值推断，不作为事实源）。"""
        key = str(open_id or "").strip()
        if not key:
            return {}
        rows = self._read_all()
        out: dict[str, str] = {}
        for r in reversed(rows):
            if str(r.get("open_id") or "").strip() != key:
                continue
            symbol = str(r.get("symbol") or "").strip().upper()
            interval = str(r.get("interval") or "").strip().lower()
            if symbol and "symbol" not in out:
                out["symbol"] = symbol
            if interval and "interval" not in out:
                out["interval"] = interval
            if len(out) >= 2:
                break
        return out

    def search_long_term(
        self,
        *,
        open_id: str,
        query: str,
        top_k: int = 3,
        history_days: int | None = None,
    ) -> list[dict[str, Any]]:
        """长期记忆检索（弱补充，不作为主事实源）。"""
        key = str(open_id or "").strip()
        q = str(query or "").strip()
        if not key or not q:
            return []
        rows = self._read_all()
        max_days = int(history_days or self.history_days)
        cutoff = time.time() - max_days * 86400
        docs = [
            r
            for r in rows
            if str(r.get("open_id") or "").strip() == key
            and float(r.get("created_ts") or 0.0) >= cutoff
        ]
        if not docs:
            return []
        return _rank_docs(query=q, docs=docs, top_k=max(1, int(top_k)))

    def compact(self) -> None:
        """压缩历史文件（删除过期记录）。"""
        rows = self._read_all()
        if not rows:
            return
        cutoff = time.time() - self.history_days * 86400
        rows = [r for r in rows if float(r.get("created_ts") or 0.0) >= cutoff]
        by_user: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            key = str(r.get("open_id") or "").strip()
            if not key:
                continue
            by_user.setdefault(key, []).append(r)
        out_rows: list[dict[str, Any]] = []
        for key, items in by_user.items():
            items.sort(key=lambda x: float(x.get("created_ts") or 0.0))
            out_rows.extend(items[-self.max_messages_per_user:])
        out_rows.sort(key=lambda x: float(x.get("created_ts") or 0.0))
        with self._lock:
            with self.path.open("w", encoding="utf-8") as f:
                for r in out_rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines:
            s = line.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out


def _rank_docs(*, query: str, docs: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
    """基于 TF-IDF 的文档排序（弱补充）。"""
    q_tokens = _tokenize(query)
    if not q_tokens:
        return []
    doc_tokens = [_tokenize(_doc_text(d)) for d in docs]
    df: dict[str, int] = {}
    for toks in doc_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    n_docs = len(doc_tokens)
    idf = {t: math.log((1 + n_docs) / (1 + cnt)) + 1.0 for t, cnt in df.items()}
    q_vec = _tfidf(q_tokens, idf)
    q_norm = _norm(q_vec)
    if q_norm <= 1e-12:
        return []
    scored: list[tuple[int, float]] = []
    for i, toks in enumerate(doc_tokens):
        d_vec = _tfidf(toks, idf)
        d_norm = _norm(d_vec)
        if d_norm <= 1e-12:
            continue
        sim = _dot(q_vec, d_vec) / (q_norm * d_norm)
        if sim > 0:
            scored.append((i, sim))
    scored.sort(key=lambda x: x[1], reverse=True)
    out: list[dict[str, Any]] = []
    for idx, score in scored[:top_k]:
        d = docs[idx]
        out.append({
            "score": round(score, 6),
            "open_id": d.get("open_id"),
            "role": d.get("role"),
            "text": str(d.get("text") or "")[:240],
            "symbol": d.get("symbol"),
            "interval": d.get("interval"),
            "created_ts": d.get("created_ts"),
        })
    return out


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    for m in _TOKEN_RE.finditer(text or ""):
        tok = m.group(0).lower()
        if tok:
            out.append(tok)
            if "_" in tok:
                out.extend([x for x in tok.split("_") if x])
    return out


def _doc_text(doc: dict[str, Any]) -> str:
    return f"{doc.get('text') or ''} {doc.get('symbol') or ''} {doc.get('interval') or ''}"


def _tfidf(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    tf: dict[str, float] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0.0) + 1.0
    if tokens:
        inv = 1.0 / float(len(tokens))
        for k in list(tf.keys()):
            tf[k] = tf[k] * inv * idf.get(k, 0.0)
    return tf


def _norm(vec: dict[str, float]) -> float:
    return math.sqrt(sum(v * v for v in vec.values())) if vec else 0.0


def _dot(a: dict[str, float], b: dict[str, float]) -> float:
    out = 0.0
    for k, v in a.items():
        bv = b.get(k)
        if bv is not None:
            out += v * bv
    return out