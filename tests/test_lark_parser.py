"""
Tier 1C: Inbound Post Rich-Text Parser.

Source: Hermes FeishuAdapter _parse_post_content / _walk_post_element
(feishu.py ~2200-2450).

Parses Lark `post` message content into a plain-text representation,
handling: text, a, at, img, emoji, table, code_block, mention_doc,
hr, and unknown elements.
"""

import pytest
from review_agent.lark.parser import parse_post_content


# ── Basic elements ──────────────────────────────────────────────

def test_parse_simple_text():
    content = {"zh_cn": {"title": "Title", "content": [
        [{"tag": "text", "text": "hello world"}],
    ]}}
    result = parse_post_content(content)
    assert "hello world" in result
    assert result.startswith("Title")


def test_parse_link_element():
    content = {"zh_cn": {"title": "", "content": [
        [{"tag": "a", "text": "click here", "href": "https://example.com"}],
    ]}}
    result = parse_post_content(content)
    assert "click here" in result
    assert "https://example.com" in result


def test_parse_at_element():
    content = {"zh_cn": {"title": "", "content": [
        [{"tag": "at", "user_id": "ou_123", "user_name": "张三"}],
    ]}}
    result = parse_post_content(content)
    assert "张三" in result


def test_parse_img_element():
    content = {"zh_cn": {"title": "", "content": [
        [{"tag": "img", "image_key": "img_abc"}],
    ]}}
    result = parse_post_content(content)
    assert "[图片]" in result


def test_parse_emoji_element():
    content = {"zh_cn": {"title": "", "content": [
        [{"tag": "emoji", "emoji_type": "OK"}],
    ]}}
    result = parse_post_content(content)
    assert "[OK]" in result


# ── Composite / structural elements ────────────────────────────

def test_parse_table():
    """Table: each row becomes a |-separated line, header bolded with **."""
    content = {"zh_cn": {"title": "", "content": [
        [{"tag": "table", "table": {
            "header_row": [["Col A"], ["Col B"]],
            "table_rows": [
                [["a1"], ["b1"]],
                [["a2"], ["b2"]],
            ],
        }}],
    ]}}
    result = parse_post_content(content)
    assert "Col A | Col B" in result
    assert "a1 | b1" in result
    assert "a2 | b2" in result


def test_parse_code_block():
    content = {"zh_cn": {"title": "", "content": [
        [{"tag": "code_block", "language": "python", "text": "print('hello')"}],
    ]}}
    result = parse_post_content(content)
    assert "python" in result
    assert "print('hello')" in result


def test_parse_mention_doc():
    content = {"zh_cn": {"title": "", "content": [
        [{"tag": "mention_doc", "title": "Design Doc v2", "url": "https://feishu.cn/docx/abc"}],
    ]}}
    result = parse_post_content(content)
    assert "Design Doc v2" in result
    assert "https://feishu.cn/docx/abc" in result


def test_parse_hr():
    content = {"zh_cn": {"title": "", "content": [
        [{"tag": "hr"}],
    ]}}
    result = parse_post_content(content)
    assert "---" in result


# ── Edge cases ─────────────────────────────────────────────────

def test_unknown_tag_preserved():
    """Unknown tags should be gracefully passed through."""
    content = {"zh_cn": {"title": "", "content": [
        [{"tag": "future_tag", "data": "something"}],
    ]}}
    result = parse_post_content(content)
    assert "future_tag" in result.lower() or "something" in result


def test_empty_content():
    result = parse_post_content({"zh_cn": {"title": "Empty", "content": []}})
    assert "Empty" in result


def test_multiple_paragraphs():
    content = {"zh_cn": {"title": "M", "content": [
        [{"tag": "text", "text": "p1"}],
        [{"tag": "text", "text": "p2"}],
    ]}}
    result = parse_post_content(content)
    assert "p1" in result
    assert "p2" in result


def test_mixed_inline_elements():
    content = {"zh_cn": {"title": "", "content": [
        [
            {"tag": "text", "text": "Hello "},
            {"tag": "at", "user_id": "ou_1", "user_name": "Alice"},
            {"tag": "text", "text": "! "},
            {"tag": "a", "text": "link", "href": "https://x.com"},
        ],
    ]}}
    result = parse_post_content(content)
    assert "Hello" in result
    assert "Alice" in result
    assert "link" in result
