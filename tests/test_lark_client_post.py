"""Regression tests for LarkClient JSON content wrapping.

The Lark `im/v1/messages` API expects `msg_type=post` content to be a
JSON-encoded string of the form `{"zh_cn": {"title": ..., "content": [...]}}`.
An earlier revision wrapped this in an extra `{"post": ...}` envelope, which
caused every rich-text DM to be rejected with HTTP 400 / Lark code 230001
("invalid message content"), and the dispatcher then fell back to plain text
for every finding. These tests pin the wire format.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from review_agent.lark.client import LarkClient


def _make_client_with_mock_post(monkeypatch) -> tuple[LarkClient, MagicMock]:
    client = LarkClient(app_id="cli_x", app_secret="sec_x")
    monkeypatch.setattr(client._token, "get", AsyncMock(return_value="tk"))

    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json.return_value = {"data": {"message_id": "om_test"}}

    captured: dict = {}

    async def fake_request(method, url, headers=None, json=None, params=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return response

    monkeypatch.setattr(client._http, "request", fake_request)
    return client, captured  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_send_dm_post_content_has_no_extra_post_wrapper(monkeypatch):
    """Lark im/v1/messages requires content == {"zh_cn": {...}} for msg_type=post.
    A {"post": {"zh_cn": ...}} wrapper triggers 230001 invalid-message-content.
    """
    client, captured = _make_client_with_mock_post(monkeypatch)
    paragraphs = [[{"tag": "text", "text": "hello"}]]

    await client.send_dm_post("ou_x", paragraphs, title="t")

    body = captured["json"]
    assert body["msg_type"] == "post"
    content_obj = json.loads(body["content"])

    assert "post" not in content_obj, (
        "content must NOT be wrapped in an extra 'post' key; "
        "Lark 230001 rejects {\"post\": {\"zh_cn\": ...}}"
    )
    assert "zh_cn" in content_obj
    assert content_obj["zh_cn"]["title"] == "t"
    assert content_obj["zh_cn"]["content"] == paragraphs


@pytest.mark.asyncio
async def test_send_dm_text_content_shape_unchanged(monkeypatch):
    """Sanity check: text path was always working; format must stay
    {"text": "..."} (no zh_cn wrapper for plain text)."""
    client, captured = _make_client_with_mock_post(monkeypatch)
    await client.send_dm_text("ou_x", "hi")
    content_obj = json.loads(captured["json"]["content"])
    assert content_obj == {"text": "hi"}
