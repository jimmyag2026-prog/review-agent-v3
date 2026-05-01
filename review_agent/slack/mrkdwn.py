"""
Markdown → Slack mrkdwn converter.

Slack's mrkdwn is a subset of Markdown with different rules:
- Bold: *text* (single asterisk)
- Italic: _text_ (underscore)
- Strikethrough: ~text~
- Code: `text` (inline), ```text``` (block)
- Links: <url|text>
- Lists: plain text with bullet prefix
- Headers: bold text on its own line

This pipeline converts standard markdown to Slack-compatible mrkdwn
by applying regex transformations in a safe order, using placeholder
substitutions to avoid interfering with code blocks and links.
"""
from __future__ import annotations

import re


def markdown_to_slack(text: str) -> str:
    """Convert standard Markdown to Slack mrkdwn format."""
    if not text:
        return text

    placeholders: dict[str, str] = {}
    counter = 0

    def _ph(s: str) -> str:
        nonlocal counter
        key = f"\x00PH{counter}\x00"
        counter += 1
        placeholders[key] = s
        return key

    # 1) Protect fenced code blocks: ```...```
    text = re.sub(
        r"```(\w*\n)?(.+?)```",
        lambda m: _ph("```" + m.group(2).rstrip() + "\n```"),
        text,
        flags=re.DOTALL,
    )

    # 2) Protect inline code: `...`
    text = re.sub(
        r"`([^`]+)`",
        lambda m: _ph(f"`{m.group(1)}`"),
        text,
    )

    # 3) Convert named links: [text](url) → <url|text>
    #    Must run BEFORE URL protection so the url part isn't grabbed first
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: _ph(f"<{m.group(2)}|{m.group(1)}>"),
        text,
    )

    # 4) Protect bare URLs: http(s)://...
    text = re.sub(
        r"(https?://\S+)",
        lambda m: _ph(m.group(1)),
        text,
    )

    # 5) Protect quoted lines: > prefix
    text = re.sub(
        r"(?m)^> ",
        lambda m: _ph("> "),
        text,
    )

    # 6) Convert headings: ### text → *text*
    text = re.sub(
        r"(?m)^#{1,6} (.+)$",
        lambda m: _ph(f"*{m.group(1)}*"),
        text,
    )

    # 7) Horizontal rules: --- or ***
    text = re.sub(
        r"(?m)^[-*]{3,}$",
        lambda m: _ph("─" * 32),
        text,
    )

    # 8) Convert unordered lists: - / *  → • (Slack bullet)
    text = re.sub(
        r"(?m)^[-*] (.+)$",
        lambda m: _ph(f"• {m.group(1)}"),
        text,
    )

    # 9) Convert ordered lists: 1. → 1.
    #    Slack supports ordered lists natively, but we normalize spacing
    text = re.sub(
        r"(?m)^(\d+)\. (.+)$",
        lambda m: _ph(f"{m.group(1)}. {m.group(2)}"),
        text,
    )

    # 10) Convert bold: **text** → *text* (Slack bold)
    text = re.sub(
        r"\*\*(.+?)\*\*",
        lambda m: _ph(f"*{m.group(1)}*"),
        text,
    )

    # 11) Convert italic: single *text* → _text_ (Slack italic)
    #     Must run AFTER bold conversion
    text = re.sub(
        r"(?<!\*)\*([^*\n]+)\*(?!\*)",
        lambda m: _ph(f"_{m.group(1)}_"),
        text,
    )

    # 12) Convert strikethrough: ~~text~~ → ~text~
    text = re.sub(
        r"~~(.+?)~~",
        lambda m: _ph(f"~{m.group(1)}~"),
        text,
    )

    # 13) Restore placeholders in reverse order
    for key in reversed(placeholders):
        text = text.replace(key, placeholders[key])

    return text


# ── Quick text-only convenience (no markdown conversion) ──

MAX_MESSAGE_LENGTH = 39000  # Slack allows 40,000; buffer left


def truncate_for_slack(text: str, max_len: int = MAX_MESSAGE_LENGTH) -> str:
    """Truncate text to fit within Slack's message length limit."""
    if len(text) <= max_len:
        return text
    suffix = "\n…(truncated for Slack length limit)"
    return text[: max_len - len(suffix)] + suffix


def escape_slack_special(text: str) -> str:
    """Escape characters that have special meaning in Slack mrkdwn.
    
    Characters: &, <, >
    """
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text
