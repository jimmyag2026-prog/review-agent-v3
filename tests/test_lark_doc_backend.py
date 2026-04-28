"""LarkDocBackend — URL extraction + fetch via mocked Lark client."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from review_agent.pipeline.ingest_backends import (
    IngestRejected,
    LarkDocBackend,
    extract_lark_urls,
)


def test_extract_docx_url():
    urls = extract_lark_urls("see this https://acme.feishu.cn/docx/CtJtdMxxx for context")
    assert urls == [("https://acme.feishu.cn/docx/CtJtdMxxx", "docx", "CtJtdMxxx")]


def test_extract_wiki_url():
    urls = extract_lark_urls("wiki link https://x.feishu.cn/wiki/wikcnABCDEF and more")
    assert urls == [("https://x.feishu.cn/wiki/wikcnABCDEF", "wiki", "wikcnABCDEF")]


def test_extract_dedup():
    text = "https://a.feishu.cn/docx/X same again https://a.feishu.cn/docx/X"
    urls = extract_lark_urls(text)
    assert len(urls) == 1


def test_extract_larksuite_intl():
    urls = extract_lark_urls("https://acme.larksuite.com/docx/AbCdEf123")
    assert urls and urls[0][1] == "docx" and urls[0][2] == "AbCdEf123"


def test_extract_no_lark_url():
    assert extract_lark_urls("https://google.com no lark here") == []
    assert extract_lark_urls("") == []


def test_extract_mixed_picks_lark():
    urls = extract_lark_urls(
        "see https://example.com/page and https://x.feishu.cn/docx/abc"
    )
    assert len(urls) == 1 and urls[0][1] == "docx"


@pytest.mark.asyncio
async def test_fetch_docx_calls_get_doc_raw():
    lark = AsyncMock()
    lark.get_doc_raw = AsyncMock(return_value="hello from lark doc body")
    backend = LarkDocBackend(lark_client=lark)
    result = await backend.fetch_lark_urls(
        [("https://x.feishu.cn/docx/T1", "docx", "T1")]
    )
    lark.get_doc_raw.assert_awaited_once_with("T1")
    assert "hello from lark doc body" in result.normalized
    assert "Lark 文档" in result.normalized


@pytest.mark.asyncio
async def test_fetch_wiki_resolves_then_fetches():
    lark = AsyncMock()
    lark.get_wiki_node = AsyncMock(return_value={"obj_token": "doc-real-id"})
    lark.get_doc_raw = AsyncMock(return_value="wiki body content")
    backend = LarkDocBackend(lark_client=lark)
    result = await backend.fetch_lark_urls(
        [("https://x.feishu.cn/wiki/wkABCD", "wiki", "wkABCD")]
    )
    lark.get_wiki_node.assert_awaited_once_with("wkABCD")
    lark.get_doc_raw.assert_awaited_once_with("doc-real-id")
    assert "wiki body content" in result.normalized


@pytest.mark.asyncio
async def test_fetch_all_fail_raises_rejected():
    lark = AsyncMock()
    lark.get_doc_raw = AsyncMock(side_effect=Exception("403 forbidden"))
    backend = LarkDocBackend(lark_client=lark)
    with pytest.raises(IngestRejected, match="bot 是否被加入"):
        await backend.fetch_lark_urls(
            [("https://x.feishu.cn/docx/T1", "docx", "T1")]
        )


@pytest.mark.asyncio
async def test_fetch_partial_success():
    """If some URLs succeed and some fail, return the successes + flag failures."""
    lark = AsyncMock()
    calls = {"a": "alpha body", "b": Exception("permission denied")}

    async def fake_get(token):
        v = calls[token]
        if isinstance(v, Exception):
            raise v
        return v
    lark.get_doc_raw = AsyncMock(side_effect=fake_get)

    backend = LarkDocBackend(lark_client=lark)
    result = await backend.fetch_lark_urls([
        ("https://x.feishu.cn/docx/a", "docx", "a"),
        ("https://x.feishu.cn/docx/b", "docx", "b"),
    ])
    assert "alpha body" in result.normalized
    assert "permission denied" in result.normalized


@pytest.mark.asyncio
async def test_fetch_no_lark_client_raises():
    backend = LarkDocBackend(lark_client=None)
    with pytest.raises(IngestRejected, match="lark_client"):
        await backend.fetch_lark_urls([("https://x.feishu.cn/docx/X", "docx", "X")])
