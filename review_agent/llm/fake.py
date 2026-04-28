"""FakeLLMClient — deterministic responses for tests, no network."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from .base import LLMClient, LLMResponse


class FakeLLMClient(LLMClient):
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._scripted: dict[str, list[str]] = defaultdict(list)
        self._fallback: Callable[[str, str | None], str] | None = None

    def script(self, model: str, *responses: str) -> None:
        self._scripted[model].extend(responses)

    def set_fallback(self, fn: Callable[[str, str | None], str]) -> None:
        self._fallback = fn

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
        self.calls.append({
            "model": model, "system": system, "user": user,
            "max_tokens": max_tokens, "temperature": temperature,
        })
        if self._scripted[model]:
            content = self._scripted[model].pop(0)
        elif self._fallback:
            content = self._fallback(user, system)
        else:
            content = "OK"
        return LLMResponse(
            content=content, reasoning="",
            finish_reason="stop",
            prompt_tokens=len(user) // 4,
            completion_tokens=len(content) // 4,
            reasoning_tokens=0, cache_hit_tokens=0,
            model=model, latency_ms=1,
        )
