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
