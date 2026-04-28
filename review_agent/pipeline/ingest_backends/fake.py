from __future__ import annotations

from pathlib import Path

from .base import IngestBackend, IngestResult


class FakeIngestBackend(IngestBackend):
    """For tests — returns a fixed normalized payload regardless of input."""

    name = "fake"
    kind = "text"

    def __init__(self, normalized: str = "FIXTURE NORMALIZED CONTENT"):
        self.normalized = normalized

    def can_handle(self, mime: str, ext: str) -> bool:
        return True

    async def ingest(self, input_path: Path) -> IngestResult:
        return IngestResult(backend="fake", normalized=self.normalized)
