from __future__ import annotations

from pathlib import Path

from .base import IngestBackend, IngestResult


class TextBackend(IngestBackend):
    name = "text"
    kind = "text"

    def can_handle(self, mime: str, ext: str) -> bool:
        return mime.startswith("text/") or ext.lower() in {".md", ".txt", ".markdown"}

    async def ingest(self, input_path: Path) -> IngestResult:
        self.validate_size(input_path.stat().st_size)
        body = input_path.read_text(encoding="utf-8", errors="replace")
        return IngestResult(backend=self.name, normalized=body)
