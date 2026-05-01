"""
Tier 1A: HTTP Retry on Send (429 rate-limit / 5xx server error).
Tier 1B: Token-retry on application-level code 99991663.

Source: Hermes FeishuAdapter _post_with_retry (feishu.py ~1204-1265)
"""

import pytest
import respx
import httpx
from review_agent.lark.client import LarkClient


_TOKEN_OK = {
    "code": 0,
    "tenant_access_token": "t-token-test-abc",
    "expire": 7200,
}


# ═════════════════════════════════════════════════════════════════
# Tier 1B token retry: retry on code 99991663 (token expired)
# ═════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_retry_on_token_expired_then_succeed():
    """1 retry on token expiry (code 99991663), then succeed."""
    async with respx.mock(
        base_url="https://open.feishu.cn",
        assert_all_called=False,
    ) as router:
        # Always return ok for token endpoint
        router.post("/open-apis/auth/v3/tenant_access_token/internal").respond(200, json=_TOKEN_OK)

        call_count = 0

        async def _intercept(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    200, json={"code": 99991663, "msg": "user access token invalid"},
                )
            return httpx.Response(
                200, json={"code": 0, "data": {"message_id": "msg_fresh"}},
            )

        router.route(method="POST", path__regex=r"/open-apis/im/.*").mock(side_effect=_intercept)

        async with httpx.AsyncClient(base_url="https://open.feishu.cn") as real_client:
            client = LarkClient(app_id="app", app_secret="sec", http=real_client)
            msg_id = await client.send_dm_text("ou_test", "hello")
        assert "fresh" in msg_id
        assert call_count == 2


# ═════════════════════════════════════════════════════════════════
# Tier 1A retry — basic cases
# ═════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_no_retry_on_2xx():
    async with respx.mock(base_url="https://open.feishu.cn", assert_all_called=False) as router:
        router.post("/open-apis/auth/v3/tenant_access_token/internal").respond(200, json=_TOKEN_OK)
        route = router.post("/open-apis/im/v1/messages?receive_id_type=open_id").respond(
            200, json={"code": 0, "data": {"message_id": "msg_ok"}},
        )

        async with httpx.AsyncClient(base_url="https://open.feishu.cn") as real_client:
            client = LarkClient(app_id="app", app_secret="sec", http=real_client)
            msg_id = await client.send_dm_text("ou_test", "hello")
        assert "ok" in msg_id
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_no_retry_on_4xx_non_429():
    async with respx.mock(base_url="https://open.feishu.cn", assert_all_called=False) as router:
        router.post("/open-apis/auth/v3/tenant_access_token/internal").respond(200, json=_TOKEN_OK)
        route = router.post("/open-apis/im/v1/messages?receive_id_type=open_id").respond(
            400, json={"code": 12345, "msg": "bad"},
        )

        async with httpx.AsyncClient(base_url="https://open.feishu.cn") as real_client:
            client = LarkClient(app_id="app", app_secret="sec", http=real_client)
            with pytest.raises(httpx.HTTPStatusError):
                await client.send_dm_text("ou_test", "bad")
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_retry_on_429_with_retry_after():
    async with respx.mock(base_url="https://open.feishu.cn", assert_all_called=False) as router:
        router.post("/open-apis/auth/v3/tenant_access_token/internal").respond(200, json=_TOKEN_OK)

        call_count = 0

        async def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(
                    429,
                    json={"code": -1, "msg": "too many requests"},
                    headers={"Retry-After": "0.02"},
                )
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "msg_retried"}})

        router.route(method="POST", path__regex=r"/open-apis/im/.*").mock(side_effect=_handler)

        async with httpx.AsyncClient(base_url="https://open.feishu.cn") as real_client:
            client = LarkClient(app_id="app", app_secret="sec", http=real_client)
            msg_id = await client.send_dm_text("ou_test", "hello")
        assert "retried" in msg_id
        assert call_count == 3


@pytest.mark.asyncio
async def test_retry_on_429_without_retry_after():
    async with respx.mock(base_url="https://open.feishu.cn", assert_all_called=False) as router:
        router.post("/open-apis/auth/v3/tenant_access_token/internal").respond(200, json=_TOKEN_OK)

        call_count = 0

        async def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return httpx.Response(429, json={"code": -1, "msg": "too many requests"})
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "msg_retried"}})

        router.route(method="POST", path__regex=r"/open-apis/im/.*").mock(side_effect=_handler)

        async with httpx.AsyncClient(base_url="https://open.feishu.cn") as real_client:
            client = LarkClient(app_id="app", app_secret="sec", http=real_client)
            msg_id = await client.send_dm_text("ou_test", "hello")
        assert "retried" in msg_id
        assert call_count == 2


@pytest.mark.asyncio
async def test_retry_on_5xx():
    async with respx.mock(base_url="https://open.feishu.cn", assert_all_called=False) as router:
        router.post("/open-apis/auth/v3/tenant_access_token/internal").respond(200, json=_TOKEN_OK)

        call_count = 0

        async def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return httpx.Response(503, json={"code": -1, "msg": "server error"})
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "msg_retried"}})

        router.route(method="POST", path__regex=r"/open-apis/im/.*").mock(side_effect=_handler)

        async with httpx.AsyncClient(base_url="https://open.feishu.cn") as real_client:
            client = LarkClient(app_id="app", app_secret="sec", http=real_client)
            msg_id = await client.send_dm_text("ou_test", "hello")
        assert "retried" in msg_id
        assert call_count == 3


@pytest.mark.asyncio
async def test_exhaust_retries_raises():
    async with respx.mock(base_url="https://open.feishu.cn", assert_all_called=False) as router:
        router.post("/open-apis/auth/v3/tenant_access_token/internal").respond(200, json=_TOKEN_OK)
        route = router.route(method="POST", path__regex=r"/open-apis/im/.*").mock(
            return_value=httpx.Response(503, json={"code": -1, "msg": "dead"})
        )

        async with httpx.AsyncClient(base_url="https://open.feishu.cn") as real_client:
            client = LarkClient(app_id="app", app_secret="sec", http=real_client)
            with pytest.raises(httpx.HTTPStatusError):
                await client.send_dm_text("ou_test", "rip")
        assert route.call_count == 4  # initial + 3 retries


@pytest.mark.asyncio
async def test_retry_on_get_429():
    async with respx.mock(base_url="https://open.feishu.cn", assert_all_called=False) as router:
        router.post("/open-apis/auth/v3/tenant_access_token/internal").respond(200, json=_TOKEN_OK)

        call_count = 0

        async def _handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return httpx.Response(429, json={"code": -1})
            return httpx.Response(200, json={"data": {"user": {"name": "test"}}})

        router.route(method="GET").mock(side_effect=_handler)

        async with httpx.AsyncClient(base_url="https://open.feishu.cn") as real_client:
            client = LarkClient(app_id="app", app_secret="sec", http=real_client)
            user = await client.get_user("ou_test")
        assert user["name"] == "test"
        assert call_count == 2
