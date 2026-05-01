"""
Lark post (rich-text) message content parser.

Parses Lark's `post` message Content.Body format into plain-text
suitable for downstream processing (session ingestion, display, etc.).

Supports: text, a (link), at (@mention), img, emoji, table,
code_block, mention_doc, hr, and unknown elements (graceful fallback).

Source: Hermes FeishuAdapter _parse_post_content / _walk_post_element
"""
from __future__ import annotations

from typing import Any

# ═══════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════


def parse_post_content(content: dict[str, Any]) -> str:
    """Parse a Lark `post` message content dict into plain-text.

    `content` is the parsed JSON of the `content` field on a
    msg_type=post message, shape:

        {"zh_cn": {"title": "...", "content": [[...], ...]}}

    Returns a human-readable plain-text string.
    """
    zh = content.get("zh_cn", {})
    lines: list[str] = []
    title = zh.get("title", "")
    if title:
        lines.append(title)

    paragraphs: list[list[dict[str, Any]]] = zh.get("content", [])
    for para in paragraphs:
        line = _walk_paragraph(para)
        if line:
            lines.append(line)

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════


def _walk_paragraph(para: list[dict[str, Any]]) -> str:
    """Walk a single paragraph (list of inline elements)."""
    parts: list[str] = []
    for el in para:
        tag = el.get("tag", "")
        handler = _ELEMENT_HANDLERS.get(tag)
        if handler:
            parts.append(handler(el))
        else:
            parts.append(_fallback(el))
    return "".join(parts)


def _fallback(el: dict[str, Any]) -> str:
    """Catch-all for unknown elements."""
    # Try to extract any text-like field
    for key in ("text", "data", "title"):
        if key in el:
            return str(el[key])
    # Last resort: tag name as hint
    tag = el.get("tag", "unknown")
    return f"[{tag}]"


# ── Per-element handlers ────────────────────────────────────────


def _text(el: dict[str, Any]) -> str:
    return el.get("text", "")


def _link(el: dict[str, Any]) -> str:
    text = el.get("text", "")
    href = el.get("href", "")
    if href and href != text:
        return f"{text}({href})"
    return text or href


def _at(el: dict[str, Any]) -> str:
    name = el.get("user_name", "") or el.get("user_id", "")
    return f"@{name}"


def _img(el: dict[str, Any]) -> str:
    return "[图片]"


def _emoji(el: dict[str, Any]) -> str:
    emoji_type = el.get("emoji_type", "")
    return f"[{emoji_type}]"


def _table(el: dict[str, Any]) -> str:
    table_data = el.get("table", {})
    lines: list[str] = []

    header = table_data.get("header_row", [])
    if header:
        cells = [_inline_cell(c) for c in header]
        lines.append(" | ".join(cells))

    rows = table_data.get("table_rows", [])
    for row in rows:
        cells = [_inline_cell(c) for c in row]
        lines.append(" | ".join(cells))

    return "\n".join(lines)


def _inline_cell(cell: list[Any]) -> str:
    """Flatten a table cell to a string.

    Lark table cells can be:
      - a list of dict element objects (rich inline)
      - a list of plain strings (legacy / certain API versions)
    """
    if not cell:
        return ""
    # Plain-string list?
    if isinstance(cell[0], str):
        return " ".join(str(c) for c in cell if isinstance(c, str))
    # Rich element dicts
    return _walk_paragraph(cell)  # type: ignore[arg-type]


def _code_block(el: dict[str, Any]) -> str:
    lang = el.get("language", "")
    text = el.get("text", "")
    header = f"[code:{lang}]" if lang else "[code]"
    return f"{header}\n{text}"


def _mention_doc(el: dict[str, Any]) -> str:
    title = el.get("title", "")
    url = el.get("url", "") or el.get("redirect_link", "")
    if url:
        return f"📄 {title} ({url})"
    return f"📄 {title}"


def _hr(el: dict[str, Any]) -> str:
    return "---"


_ELEMENT_HANDLERS: dict[str, Any] = {
    "text": _text,
    "a": _link,
    "at": _at,
    "img": _img,
    "emoji": _emoji,
    "table": _table,
    "code_block": _code_block,
    "mention_doc": _mention_doc,
    "hr": _hr,
}
