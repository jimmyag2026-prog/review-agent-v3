from __future__ import annotations

import asyncio
import time

import httpx

from ..util import log
from .base import LLMClient, LLMResponse, LLMTerminalFailure

_logger = log.get(__name__)


class DeepSeekClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.deepseek.com/v1",
        client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = client
        self._owned_client = client is None
        self.max_retries = max_retries

    async def aclose(self) -> None:
        if self._owned_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        return self._client

    async def chat(
        self,
        *,
        system: str | None,
        user: str,
        model: str,
        max_tokens: int = 8192,
        temperature: float = 0.3,
        timeout: int = 120,
    ) -> LLMResponse:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            t0 = time.monotonic()
            try:
                resp = await self._http().post(
                    url, json=body, headers=headers, timeout=timeout,
                )
                latency_ms = int((time.monotonic() - t0) * 1000)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"retryable {resp.status_code}", request=resp.request, response=resp,
                    )
                resp.raise_for_status()
                env = resp.json()
                msg = env["choices"][0]["message"]
                usage = env.get("usage", {})
                return LLMResponse(
                    content=msg.get("content", "") or "",
                    reasoning=msg.get("reasoning_content", "") or "",
                    finish_reason=env["choices"][0].get("finish_reason", ""),
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    reasoning_tokens=usage.get("completion_tokens_details", {}).get(
                        "reasoning_tokens", 0
                    ),
                    cache_hit_tokens=usage.get("prompt_cache_hit_tokens", 0),
                    model=env.get("model", model),
                    latency_ms=latency_ms,
                )
            except (httpx.HTTPError, KeyError, ValueError) as e:
                last_err = e
                wait = 2 ** attempt * 5
                _logger.warning(
                    "deepseek attempt %d/%d failed: %s; retrying in %ds",
                    attempt + 1, self.max_retries, e, wait,
                )
                if attempt + 1 < self.max_retries:
                    await asyncio.sleep(wait)
        raise LLMTerminalFailure(f"deepseek after {self.max_retries}: {last_err}") from last_err
