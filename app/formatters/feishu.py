from __future__ import annotations

_MAX_FEISHU_MESSAGE_CHARS = 4000


def split_feishu_text(text: str, max_len: int = _MAX_FEISHU_MESSAGE_CHARS) -> list[str]:
    """飞书单条消息长度限制下的分段（仅格式层，不决定内容重点）。"""
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= max_len:
        return [t]
    parts: list[str] = []
    buf: list[str] = []
    acc = 0
    for block in t.split("\n\n"):
        extra = len(block) + (2 if buf else 0)
        if acc + extra <= max_len:
            buf.append(block)
            acc += extra
            continue
        if buf:
            parts.append("\n\n".join(buf))
        buf = []
        acc = 0
        if len(block) <= max_len:
            buf.append(block)
            acc = len(block)
        else:
            for i in range(0, len(block), max_len):
                parts.append(block[i : i + max_len])
    if buf:
        parts.append("\n\n".join(buf))
    return parts if parts else [t[:max_len]]
