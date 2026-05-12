from __future__ import annotations


def split_plain_text(text: str, max_len: int = 8000) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= max_len:
        return [t]
    return [t[i : i + max_len] for i in range(0, len(t), max_len)]
