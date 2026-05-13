"""本地 RAG 层：检索可信事实（三层重构核心模块）。

职责：
1. 统一索引本地结构化产物
2. 支持按 symbol、interval、source_type、日期过滤
3. 支持 recent-first + score 排序

关键改变：
- 新增元数据字段：interval、provider、created_ts、session_key、task_type_hint
- RAG 层必须是事实主源，优先顺序：ai_overview.json → full_report.md → ai_brief.md → research
- 输出必须带来源信息（source_path、source_type、symbol、interval、score、snippet）
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

SourceType = Literal["kline", "research", "memory", "journal", "overview", "brief", "report"]


@dataclass
class RagChunk:
    """RAG 检索单元（按文档 8.2 契约）。"""
    chunk_id: str
    text: str
    source_path: str
    source_type: SourceType
    symbol: str | None = None
    interval: str | None = None
    provider: str | None = None
    created_ts: float | None = None
    session_key: str | None = None
    task_type_hint: str | None = None  # analysis / research
    score: float = 0.0

    def to_hit_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "score": round(self.score, 6),
            "source_path": self.source_path,
            "source_type": self.source_type,
            "symbol": self.symbol,
            "interval": self.interval,
            "created_ts": self.created_ts,
            "snippet": self.text[:240],
        }


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    for m in _TOKEN_RE.finditer(text or ""):
        tok = m.group(0).lower()
        if tok:
            out.append(tok)
            if "_" in tok:
                out.extend([x for x in tok.split("_") if x])
    return out


class RagIndex:
    """RAG 索引：支持过滤 + recent-first 排序。"""

    def __init__(self, chunks: list[RagChunk]) -> None:
        self._chunks = chunks
        self._idf: dict[str, float] = {}
        self._chunk_vecs: list[dict[str, float]] = []
        self._chunk_norms: list[float] = []
        if chunks:
            self._build()

    @property
    def chunks(self) -> list[RagChunk]:
        return self._chunks

    @classmethod
    def from_output_root(
        cls,
        output_root: Path,
        *,
        memory_paths: list[Path] | None = None,
        max_age_hours: int | None = None,
    ) -> "RagIndex":
        """从 output 目录构建索引。

        Args:
            output_root: output 目录路径
            memory_paths: 可选的飞书历史 JSONL 路径
            max_age_hours: 只索引最近 N 小时的产物（None 表示全部）
        """
        cutoff_ts: float | None = None
        if max_age_hours is not None:
            cutoff_ts = time.time() - max_age_hours * 3600

        chunks: list[RagChunk] = []

        # 优先级最高：ai_overview.json
        for p in output_root.rglob("ai_overview.json"):
            chunks.extend(_chunks_from_overview(p, cutoff_ts=cutoff_ts))

        # 次选：full_report.md
        for p in output_root.rglob("full_report.md"):
            chunks.extend(_chunks_from_text_file(
                p, source_type="report", cutoff_ts=cutoff_ts
            ))

        # ai_brief.md
        for p in output_root.rglob("ai_brief.md"):
            chunks.extend(_chunks_from_text_file(
                p, source_type="brief", cutoff_ts=cutoff_ts
            ))

        # 研报 JSON
        for p in output_root.rglob("*_research.json"):
            chunks.extend(_chunks_from_research_json(p, cutoff_ts=cutoff_ts))

        # 研报 Markdown
        for p in output_root.rglob("*_research.md"):
            chunks.extend(_chunks_from_text_file(
                p, source_type="research", cutoff_ts=cutoff_ts
            ))

        # 长期记忆（可选）
        for p in (memory_paths or []):
            chunks.extend(_chunks_from_memory_jsonl(p, cutoff_ts=cutoff_ts))

        # 按 created_ts 降序排序（recent-first）
        chunks.sort(key=lambda c: c.created_ts or 0.0, reverse=True)

        return cls(chunks)

    def _build(self) -> None:
        docs_tokens = [_tokenize(c.text) for c in self._chunks]
        df: dict[str, int] = {}
        for toks in docs_tokens:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        n_docs = len(docs_tokens)
        self._idf = {t: math.log((1 + n_docs) / (1 + cnt)) + 1.0 for t, cnt in df.items()}
        self._chunk_vecs = []
        self._chunk_norms = []
        for toks in docs_tokens:
            tf: dict[str, float] = {}
            for t in toks:
                tf[t] = tf.get(t, 0.0) + 1.0
            if toks:
                inv = 1.0 / float(len(toks))
                for k in list(tf.keys()):
                    tf[k] = tf[k] * inv * self._idf.get(k, 0.0)
            norm = math.sqrt(sum(v * v for v in tf.values())) if tf else 0.0
            self._chunk_vecs.append(tf)
            self._chunk_norms.append(norm)

    def query(
        self,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.03,
        symbol_filter: str | None = None,
        interval_filter: str | None = None,
        source_type_filter: SourceType | None = None,
        recent_first: bool = True,
    ) -> list[dict[str, Any]]:
        """检索事实。

        Args:
            query: 查询文本
            top_k: 返回数量
            min_score: 最小分数阈值
            symbol_filter: 标的过滤
            interval_filter: 周期过滤
            source_type_filter: 来源类型过滤
            recent_first: 是否优先返回最近产物

        Returns:
            命中结果列表，每项包含 chunk_id、score、source_path、source_type、symbol、interval、snippet
        """
        q_tokens = _tokenize(query)
        if not q_tokens or not self._chunks:
            return []

        q_tf: dict[str, float] = {}
        for t in q_tokens:
            q_tf[t] = q_tf.get(t, 0.0) + 1.0
        inv = 1.0 / float(len(q_tokens))
        for k in list(q_tf.keys()):
            q_tf[k] = q_tf[k] * inv * self._idf.get(k, 0.0)
        q_norm = math.sqrt(sum(v * v for v in q_tf.values())) if q_tf else 0.0
        if q_norm <= 1e-12:
            return []

        scored: list[tuple[int, float]] = []
        for i, vec in enumerate(self._chunk_vecs):
            c = self._chunks[i]

            # 过滤
            if symbol_filter and c.symbol != symbol_filter.upper():
                continue
            if interval_filter and c.interval != interval_filter.lower():
                continue
            if source_type_filter and c.source_type != source_type_filter:
                continue

            denom = q_norm * self._chunk_norms[i]
            if denom <= 1e-12:
                continue
            dot = 0.0
            for t, qv in q_tf.items():
                dv = vec.get(t)
                if dv is not None:
                    dot += qv * dv
            sim = dot / denom
            if sim >= min_score:
                # recent_first：分数 + 时间衰减加权
                if recent_first and c.created_ts:
                    age_hours = (time.time() - c.created_ts) / 3600
                    decay = math.exp(-age_hours / 24)  # 24 小时衰减因子
                    sim = sim * (1 + 0.5 * decay)
                scored.append((i, sim))

        # 排序：分数优先，recent_first 时时间加权已体现在分数中
        scored.sort(key=lambda x: x[1], reverse=True)

        out: list[dict[str, Any]] = []
        for i, score in scored[:max(1, int(top_k))]:
            c = self._chunks[i]
            c.score = score
            out.append(c.to_hit_dict())
        return out

    def get_latest_overview_for_symbol(
        self,
        symbol: str,
        *,
        interval: str | None = None,
    ) -> RagChunk | None:
        """获取指定标的的最新 ai_overview（用于追问）。"""
        sym = str(symbol or "").strip().upper()
        if not sym:
            return None
        iv = str(interval or "").strip().lower() if interval else None

        best: RagChunk | None = None
        best_ts: float = 0.0
        for c in self._chunks:
            if c.source_type != "kline":
                continue
            if c.symbol != sym:
                continue
            if iv and c.interval != iv:
                continue
            ts = c.created_ts or 0.0
            if ts > best_ts:
                best = c
                best_ts = ts
        return best

    def get_facts_for_followup(
        self,
        symbol: str,
        *,
        interval: str | None = None,
        output_ref_path: str | None = None,
    ) -> dict[str, Any]:
        """为追问获取事实（优先使用 output_ref_path）。"""
        result: dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "found": False,
            "overview": None,
            "report": None,
            "research": None,
        }

        # 优先使用已记录的产物路径
        if output_ref_path:
            p = Path(output_ref_path)
            if p.exists():
                try:
                    obj = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pass
                else:
                    result["overview"] = obj
                    result["found"] = True
                    result["source_path"] = str(p)
                    return result

        # 否则从索引中检索最新
        chunk = self.get_latest_overview_for_symbol(symbol, interval=interval)
        if chunk:
            try:
                obj = json.loads(Path(chunk.source_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            else:
                result["overview"] = obj
                result["found"] = True
                result["source_path"] = chunk.source_path
                result["chunk"] = chunk.to_hit_dict()
        return result


def _extract_created_ts_from_path(path: Path) -> float | None:
    """从路径提取日期（output/provider/region/YYYY-MM-DD/...）。"""
    parts = path.parts
    for part in parts:
        if re.match(r"^\d{4}-\d{2}-\d{2}$", part):
            try:
                from datetime import datetime
                dt = datetime.strptime(part, "%Y-%m-%d")
                return dt.timestamp()
            except ValueError:
                pass
    return None


def _chunks_from_overview(path: Path, *, cutoff_ts: float | None = None) -> list[RagChunk]:
    created_ts = _extract_created_ts_from_path(path)
    if cutoff_ts and created_ts and created_ts < cutoff_ts:
        return []

    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    items = obj.get("items")
    if not isinstance(items, list):
        return []

    out: list[RagChunk] = []
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        symbol = str(it.get("symbol") or "").upper() or None
        interval = str(it.get("interval") or "").lower() or None
        provider = str(it.get("provider") or "").lower() or None
        stats = it.get("stats") if isinstance(it.get("stats"), dict) else {}
        wy = it.get("wyckoff_123_v1") if isinstance(it.get("wyckoff_123_v1"), dict) else {}
        fixed = it.get("fixed_template") if isinstance(it.get("fixed_template"), dict) else {}

        text = (
            f"symbol={symbol or 'UNKNOWN'} interval={interval} provider={provider} "
            f"trend={stats.get('trend')} last={stats.get('last')} "
            f"fib_zone={stats.get('price_vs_fib_zone')} "
            f"regime={((stats.get('market_regime') or {}).get('label') if isinstance(stats.get('market_regime'), dict) else None)} "
            f"wyckoff_side={wy.get('preferred_side')} wyckoff_aligned={wy.get('aligned')} "
            f"entry={fixed.get('触发条件') or wy.get('selected_setup', {}).get('entry')} "
            f"stop={fixed.get('止损条件') or wy.get('selected_setup', {}).get('stop')} "
            f"tp1={wy.get('selected_setup', {}).get('tp1')} tp2={wy.get('selected_setup', {}).get('tp2')} "
            f"triggered={wy.get('selected_setup', {}).get('triggered')}"
        )

        session_key = f"{symbol}_{interval}_{created_ts}" if symbol and interval and created_ts else None

        out.append(RagChunk(
            chunk_id=f"overview:{path}:{idx}",
            text=text,
            source_path=str(path),
            source_type="kline",
            symbol=symbol,
            interval=interval,
            provider=provider,
            created_ts=created_ts,
            session_key=session_key,
            task_type_hint="analysis",
        ))
    return out


def _chunks_from_research_json(path: Path, *, cutoff_ts: float | None = None) -> list[RagChunk]:
    created_ts = _extract_created_ts_from_path(path)
    if cutoff_ts and created_ts and created_ts < cutoff_ts:
        return []

    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    items = obj.get("items")
    if not isinstance(items, list):
        return []

    out: list[RagChunk] = []
    for idx, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "")
        content = str(it.get("content") or "")
        org = str(it.get("org_name") or "")
        text = f"title={title} org={org} content={content}"

        out.append(RagChunk(
            chunk_id=f"research-json:{path}:{idx}",
            text=text,
            source_path=str(path),
            source_type="research",
            created_ts=created_ts,
            task_type_hint="research",
        ))
    return out


def _chunks_from_text_file(
    path: Path,
    *,
    source_type: SourceType,
    cutoff_ts: float | None = None,
) -> list[RagChunk]:
    created_ts = _extract_created_ts_from_path(path)
    if cutoff_ts and created_ts and created_ts < cutoff_ts:
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    blocks = [x.strip() for x in text.split("\n\n") if x.strip()]
    out: list[RagChunk] = []

    # 尝试从路径/内容提取 symbol
    symbol_match = re.search(r"([A-Z]{2,4}[_\dA-Z]+)", path.stem, re.I)
    symbol = symbol_match.group(1).upper() if symbol_match else None

    interval_match = re.search(r"_(15m|30m|1h|4h|1d)", path.stem, re.I)
    interval = interval_match.group(1).lower() if interval_match else None

    for idx, block in enumerate(blocks[:40]):
        out.append(RagChunk(
            chunk_id=f"{source_type}:{path}:{idx}",
            text=block,
            source_path=str(path),
            source_type=source_type,
            symbol=symbol,
            interval=interval,
            created_ts=created_ts,
            task_type_hint="analysis" if source_type in {"report", "brief"} else "research",
        ))
    return out


def _chunks_from_memory_jsonl(path: Path, *, cutoff_ts: float | None = None) -> list[RagChunk]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    out: list[RagChunk] = []
    for idx, line in enumerate(lines[-2000:]):
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        created_ts = float(obj.get("created_ts") or 0.0)
        if cutoff_ts and created_ts < cutoff_ts:
            continue

        role = str(obj.get("role") or "")
        text = str(obj.get("text") or "")
        symbol = str(obj.get("symbol") or "").strip().upper() or None
        interval = str(obj.get("interval") or "").strip().lower() or None

        payload = f"role={role} symbol={symbol or ''} interval={interval or ''} text={text}"

        out.append(RagChunk(
            chunk_id=f"memory:{path}:{idx}",
            text=payload,
            source_path=str(path),
            source_type="memory",
            symbol=symbol,
            interval=interval,
            created_ts=created_ts,
        ))
    return out


# 全局索引缓存（可选）
_GLOBAL_RAG_INDEX: RagIndex | None = None
_GLOBAL_RAG_INDEX_LOCK = threading.Lock()


def get_or_create_rag_index(
    output_root: Path | None = None,
    *,
    force_refresh: bool = False,
    max_age_hours: int | None = 72,
) -> RagIndex:
    """获取或创建全局 RAG 索引。"""
    global _GLOBAL_RAG_INDEX

    if output_root is None:
        output_root = Path(__file__).resolve().parents[1] / "output"

    if not force_refresh and _GLOBAL_RAG_INDEX is not None:
        return _GLOBAL_RAG_INDEX

    with _GLOBAL_RAG_INDEX_LOCK:
        if not force_refresh and _GLOBAL_RAG_INDEX is not None:
            return _GLOBAL_RAG_INDEX
        _GLOBAL_RAG_INDEX = RagIndex.from_output_root(
            output_root, max_age_hours=max_age_hours
        )
        return _GLOBAL_RAG_INDEX


def refresh_rag_index() -> None:
    """强制刷新全局索引。"""
    global _GLOBAL_RAG_INDEX

    with _GLOBAL_RAG_INDEX_LOCK:
        _GLOBAL_RAG_INDEX = None