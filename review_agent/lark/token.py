"""Tenant access token cache.

NOTE: token is held in-process memory ONLY. Never persist to sqlite or
disk — it is a Lark credential and would expand the leak surface.
(round-1 N2)
"""
from __future__ import annotations

import time

import httpx


class TenantTokenCache:
    def __init__(self, app_id: str, app_secret: str, base_url: str, http: httpx.AsyncClient):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url.rstrip("/")
        self._http = http
        self._token: str | None = None
        self._expires_at: float = 0.0

    async def get(self) -> str:
        # refresh 10 min before actual expiry
        if self._token and time.time() < self._expires_at - 600:
            return self._token
        r = await self._http.post(
            f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=15.0,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("code") != 0:
            raise RuntimeError(f"tenant_access_token failed: {body}")
        self._token = body["tenant_access_token"]
        self._expires_at = time.time() + int(body.get("expire", 7200))
        return self._token
