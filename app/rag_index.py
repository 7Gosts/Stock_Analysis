from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@dataclass
class RagChunk:
    chunk_id: str
    text: str
    source_path: str
    source_type: str
    symbol: str | None = None


def _tokenize(text: str) -> list[str]:
    out: list[str] = []
    for m in _TOKEN_RE.finditer(text or ""):
        tok = m.group(0).lower()
        if not tok:
            continue
        out.append(tok)
        if "_" in tok:
            out.extend([x for x in tok.split("_") if x])
    return out


class RagIndex:
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
    ) -> "RagIndex":
        chunks: list[RagChunk] = []
        for p in output_root.rglob("ai_overview.json"):
            chunks.extend(_chunks_from_overview(p))
        for p in output_root.rglob("*_research.json"):
            chunks.extend(_chunks_from_research_json(p))
        for p in output_root.rglob("*_research.md"):
            chunks.extend(_chunks_from_text_file(p, source_type="research"))
        for p in (memory_paths or []):
            chunks.extend(_chunks_from_memory_jsonl(p))
        return cls(chunks)

    def _build(self) -> None:
        docs_tokens: list[list[str]] = [_tokenize(c.text) for c in self._chunks]
        df: dict[str, int] = {}
        for toks in docs_tokens:
            seen = set(toks)
            for t in seen:
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

    def query(self, query: str, *, top_k: int = 5, min_score: float = 0.03) -> list[dict[str, Any]]:
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
                scored.append((i, sim))
        scored.sort(key=lambda x: x[1], reverse=True)
        out: list[dict[str, Any]] = []
        for i, score in scored[: max(1, int(top_k))]:
            c = self._chunks[i]
            out.append(
                {
                    "chunk_id": c.chunk_id,
                    "score": round(score, 6),
                    "source_path": c.source_path,
                    "source_type": c.source_type,
                    "symbol": c.symbol,
                    "snippet": c.text[:240],
                }
            )
        return out


def _chunks_from_overview(path: Path) -> list[RagChunk]:
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
        stats = it.get("stats") if isinstance(it.get("stats"), dict) else {}
        text = (
            f"symbol={symbol or 'UNKNOWN'} interval={it.get('interval')} trend={stats.get('trend')} "
            f"last={stats.get('last')} fib_zone={stats.get('price_vs_fib_zone')} "
            f"regime={((stats.get('market_regime') or {}).get('label') if isinstance(stats.get('market_regime'), dict) else None)}"
        )
        out.append(
            RagChunk(
                chunk_id=f"overview:{path}:{idx}",
                text=text,
                source_path=str(path),
                source_type="kline",
                symbol=symbol,
            )
        )
    return out


def _chunks_from_research_json(path: Path) -> list[RagChunk]:
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
        out.append(
            RagChunk(
                chunk_id=f"research-json:{path}:{idx}",
                text=text,
                source_path=str(path),
                source_type="research",
            )
        )
    return out


def _chunks_from_text_file(path: Path, *, source_type: str) -> list[RagChunk]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    blocks = [x.strip() for x in text.split("\n\n") if x.strip()]
    out: list[RagChunk] = []
    for idx, block in enumerate(blocks[:40]):
        out.append(
            RagChunk(
                chunk_id=f"{source_type}:{path}:{idx}",
                text=block,
                source_path=str(path),
                source_type=source_type,
            )
        )
    return out


def _chunks_from_memory_jsonl(path: Path) -> list[RagChunk]:
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
        role = str(obj.get("role") or "")
        text = str(obj.get("text") or "")
        symbol = str(obj.get("symbol") or "").strip().upper() or None
        interval = str(obj.get("interval") or "").strip().lower()
        question = str(obj.get("question") or "").strip()
        payload = f"role={role} symbol={symbol or ''} interval={interval} question={question} text={text}"
        out.append(
            RagChunk(
                chunk_id=f"memory:{path}:{idx}",
                text=payload,
                source_path=str(path),
                source_type="memory",
                symbol=symbol,
            )
        )
    return out
