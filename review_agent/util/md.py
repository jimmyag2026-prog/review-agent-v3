from __future__ import annotations

import hashlib


def text_hash(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def line_range_snippet(text: str, start: int, end: int, *, max_chars: int = 120) -> str:
    lines = text.splitlines()
    chunk = "\n".join(lines[max(0, start - 1) : end])
    if len(chunk) > max_chars:
        chunk = chunk[: max_chars - 1] + "…"
    return chunk
