"""Send retry with message replacement (Tier 2D).

Tests for ``LarkClient.update_message`` — the Lark im/v1/messages/{id} PUT
endpoint that overwrites an existing message in-place.
"""

from __future__ import annotations

import json
import httpx
import pytest
import respx

from review_agent.lark.client import LarkClient

_FEISHU = "https://open.feishu.cn"
_FAKE_APP = "cli_test"
_FAKE_SECRET = "sec_test"


@pytest.fixture
async def http() -> httpx.AsyncClient:
    async with httpx.AsyncClient() as c:
        yield c


def _make_client(http: httpx.AsyncClient) -> LarkClient:
    return LarkClient(_FAKE_APP, _FAKE_SECRET, http=http)


@pytest.fixture
def token_route():
    """Stub token endpoint so any retry can re-fetch a token."""
    with respx.mock(base_url=_FEISHU) as router:
        router.post("/open-apis/auth/v3/tenant_access_token/internal").respond(
            status_code=200,
            json={"code": 0, "tenant_access_token": "tok_test", "expire": 7200},
        )
        yield


# ── Tier 2D: update_message ──────────────────────────────────────


@pytest.mark.asyncio
async def test_update_text_success(http: httpx.AsyncClient, token_route):
    """Update a text message returns True when Lark responds code=0."""
    msg_id = "om_abc123"
    client = _make_client(http)

    async with respx.mock(base_url=_FEISHU) as router:
        route = router.put(f"/open-apis/im/v1/messages/{msg_id}").respond(
            status_code=200,
            json={"code": 0, "msg": "ok"},
        )

        ok = await client.update_message(msg_id, "replacement text", msg_type="text")
        assert ok

        body = json.loads(route.calls[0].request.content)
        assert body["msg_type"] == "text"
        assert json.loads(body["content"])["text"] == "replacement text"


@pytest.mark.asyncio
async def test_update_post_success(http: httpx.AsyncClient, token_route):
    """Update a post message — post content is passed through unchanged."""
    msg_id = "om_post_xyz"
    client = _make_client(http)
    post_content = '{"zh_cn":{"title":"Update","content":[[{"tag":"text","text":"hi"}]]}}'

    async with respx.mock(base_url=_FEISHU) as router:
        route = router.put(f"/open-apis/im/v1/messages/{msg_id}").respond(
            status_code=200,
            json={"code": 0},
        )

        ok = await client.update_message(msg_id, post_content, msg_type="post")
        assert ok

        body = json.loads(route.calls[0].request.content)
        assert body["msg_type"] == "post"
        assert body["content"] == post_content  # raw pass-through


@pytest.mark.asyncio
async def test_update_returns_false_on_api_error(http: httpx.AsyncClient, token_route):
    """Non-zero code from Lark returns False."""
    msg_id = "om_err"
    client = _make_client(http)

    async with respx.mock(base_url=_FEISHU) as router:
        router.put(f"/open-apis/im/v1/messages/{msg_id}").respond(
            status_code=200,
            json={"code": 230001, "msg": "invalid message content"},
        )

        ok = await client.update_message(msg_id, "bad", msg_type="text")
        assert not ok


@pytest.mark.asyncio
async def test_update_retries_on_429(http: httpx.AsyncClient, token_route):
    """429 is retried — use side_effect for multi-response sequence."""
    msg_id = "om_ratelimit"
    client = _make_client(http)

    async with respx.mock(base_url=_FEISHU) as router:
        call_count = 0

        def _handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, json={"code": -1, "msg": "rate limited"})
            return httpx.Response(200, json={"code": 0})

        router.put(f"/open-apis/im/v1/messages/{msg_id}").mock(side_effect=_handler)

        ok = await client.update_message(msg_id, "retried text", msg_type="text")
        assert ok
        assert call_count == 2, f"expected 2 calls, got {call_count}"


@pytest.mark.asyncio
async def test_update_retries_on_5xx(http: httpx.AsyncClient, token_route):
    """5xx is retried."""
    msg_id = "om_server_error"
    client = _make_client(http)

    async with respx.mock(base_url=_FEISHU) as router:
        call_count = 0

        def _handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503, json={"code": -1})
            return httpx.Response(200, json={"code": 0})

        router.put(f"/open-apis/im/v1/messages/{msg_id}").mock(side_effect=_handler)

        ok = await client.update_message(msg_id, "retry 5xx", msg_type="text")
        assert ok
        assert call_count == 2


@pytest.mark.asyncio
async def test_update_returns_false_on_4xx(http: httpx.AsyncClient, token_route):
    """Non-retryable 401 — raise_for_status() caught by try/except."""
    msg_id = "om_nope"
    client = _make_client(http)

    async with respx.mock(base_url=_FEISHU) as router:
        router.put(f"/open-apis/im/v1/messages/{msg_id}").respond(
            status_code=401, json={"code": -1},
        )

        ok = await client.update_message(msg_id, "nope", msg_type="text")
        assert not ok


@pytest.mark.asyncio
async def test_update_default_msg_type_is_text(http: httpx.AsyncClient, token_route):
    """msg_type defaults to 'text'."""
    msg_id = "om_default"
    client = _make_client(http)

    async with respx.mock(base_url=_FEISHU) as router:
        route = router.put(f"/open-apis/im/v1/messages/{msg_id}").respond(
            status_code=200,
            json={"code": 0},
        )

        ok = await client.update_message(msg_id, "hello")
        assert ok
        body = json.loads(route.calls[0].request.content)
        assert body["msg_type"] == "text"
