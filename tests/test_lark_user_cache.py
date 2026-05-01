"""
Tier 2B: Sender name cache in LarkClient.get_user.

Hermes FeishuAdapter caches sender names for 10 minutes to avoid
redundant Lark contact API calls for the same user during a session.

Tests use freezegun to control time and verify cache hit/miss/expiry.
"""

import pytest
import respx
import httpx
from freezegun import freeze_time
from review_agent.lark.client import LarkClient


_TOKEN_OK = {
    "code": 0,
    "tenant_access_token": "t-token-test-abc",
    "expire": 7200,
}

_USER_RESP = {
    "data": {
        "user": {"name": "Alice", "en_name": "alice_test"},
    },
}


@pytest.mark.asyncio
async def test_get_user_caches_result():
    """Second get_user call within TTL returns cached result (no API call)."""
    async with respx.mock(base_url="https://open.feishu.cn", assert_all_called=False) as router:
        router.post("/open-apis/auth/v3/tenant_access_token/internal").respond(200, json=_TOKEN_OK)

        user_route = router.get("/open-apis/contact/v3/users/ou_test").respond(
            200, json=_USER_RESP,
        )

        async with httpx.AsyncClient(base_url="https://open.feishu.cn") as real_client:
            client = LarkClient(app_id="app", app_secret="sec", http=real_client)

            # First call — should hit API
            user1 = await client.get_user("ou_test")
            assert user1["name"] == "Alice"
            assert user_route.call_count == 1

            # Second call — should be cached
            user2 = await client.get_user("ou_test")
            assert user2["name"] == "Alice"
            assert user_route.call_count == 1  # still 1


@pytest.mark.asyncio
async def test_get_user_cache_expires():
    """After TTL expires, next get_user should re-fetch."""
    async with respx.mock(base_url="https://open.feishu.cn", assert_all_called=False) as router:
        router.post("/open-apis/auth/v3/tenant_access_token/internal").respond(200, json=_TOKEN_OK)

        user_route = router.get("/open-apis/contact/v3/users/ou_test").respond(
            200, json=_USER_RESP,
        )

        async with httpx.AsyncClient(base_url="https://open.feishu.cn") as real_client:
            client = LarkClient(app_id="app", app_secret="sec", http=real_client)

            with freeze_time("2026-01-01 12:00:00"):
                user1 = await client.get_user("ou_test")
                assert user1["name"] == "Alice"
                assert user_route.call_count == 1

            # Jump 11 minutes forward — cache should expire (TTL=10min)
            with freeze_time("2026-01-01 12:11:00"):
                user2 = await client.get_user("ou_test")
                assert user2["name"] == "Alice"
                assert user_route.call_count == 2  # re-fetched


@pytest.mark.asyncio
async def test_get_user_different_users_separate_caches():
    """Different user IDs have independent cache entries."""
    async with respx.mock(base_url="https://open.feishu.cn", assert_all_called=False) as router:
        router.post("/open-apis/auth/v3/tenant_access_token/internal").respond(200, json=_TOKEN_OK)

        router.get("/open-apis/contact/v3/users/ou_a").respond(
            200, json={"data": {"user": {"name": "Alice"}}},
        )
        bob_route = router.get("/open-apis/contact/v3/users/ou_b").respond(
            200, json={"data": {"user": {"name": "Bob"}}},
        )

        async with httpx.AsyncClient(base_url="https://open.feishu.cn") as real_client:
            client = LarkClient(app_id="app", app_secret="sec", http=real_client)

            a = await client.get_user("ou_a")
            assert a["name"] == "Alice"

            b = await client.get_user("ou_b")
            assert b["name"] == "Bob"
            assert bob_route.call_count == 1  # Bob fetched once

            # Alice still cached
            a2 = await client.get_user("ou_a")
            assert a2["name"] == "Alice"
