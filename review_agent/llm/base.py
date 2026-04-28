from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    content: str
    reasoning: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cache_hit_tokens: int
    model: str
    latency_ms: int


class LLMTerminalFailure(RuntimeError):
    """LLM failed after all retries; pipeline should mark session failed."""


class LLMOutputParseError(LLMTerminalFailure):
    """LLM returned content that couldn't be parsed (empty / non-JSON / wrong shape).
    Inherits LLMTerminalFailure so dispatcher's existing `except LLMTerminalFailure`
    catches it and walks the session through `_fail_session` instead of letting
    the worker crash silently and leave the session stuck.

    Issue #6 (live test 2026-04-28): deepseek-v4-flash returned empty content
    on a heavy 4-pillar prompt → worker crashed → session forever at SCANNING.
    """


class LLMClient(ABC):
    @abstractmethod
    async def chat(
        self,
        *,
        system: str | None,
        user: str,
        model: str,
        max_tokens: int = 8192,
        temperature: float = 0.3,
        timeout: int = 120,
    ) -> LLMResponse: ...
