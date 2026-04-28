from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class IngestResult:
    backend: str
    normalized: str
    note: str = ""


class IngestRejected(Exception):
    """Material is too big / wrong format. Friendly user_message included."""

    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class IngestUnsupported(Exception):
    """No backend can handle this mime/ext."""


# Default size guards (round-1 N3)
_DEFAULT_MAX = {
    "pdf": 20 * 1024 * 1024,
    "image": 10 * 1024 * 1024,
    "audio": 50 * 1024 * 1024,
    "text": 1 * 1024 * 1024,
}


class IngestBackend(ABC):
    name: str
    kind: str = "text"

    def can_handle(self, mime: str, ext: str) -> bool:
        return False

    def validate_size(self, size_bytes: int) -> None:
        cap = _DEFAULT_MAX.get(self.kind, 5 * 1024 * 1024)
        if size_bytes > cap:
            raise IngestRejected(
                f"材料太大了（{size_bytes // (1024*1024)}MB），超过了 {cap // (1024*1024)}MB 上限。"
                "切小一点再发一遍。"
            )

    @abstractmethod
    async def ingest(self, input_path: Path) -> IngestResult: ...
