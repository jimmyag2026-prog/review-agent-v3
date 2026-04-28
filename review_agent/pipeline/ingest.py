from __future__ import annotations

import mimetypes
from pathlib import Path

from ..core.models import Session
from ..util.path import resolve_session_path
from .ingest_backends import IngestBackend, IngestResult, IngestRejected, IngestUnsupported


class IngestPipeline:
    def __init__(self, fs_root: str, backends: list[IngestBackend]):
        self.fs_root = fs_root
        self.backends = backends

    async def run(self, session: Session, input_filename: str) -> IngestResult:
        # verify path is safely inside session
        input_path = resolve_session_path(
            self.fs_root, session.requester_oid, session.id, f"input/{input_filename}",
            must_exist=True,
        )
        out_path = resolve_session_path(
            self.fs_root, session.requester_oid, session.id, "normalized.md",
        )
        mime, _ = mimetypes.guess_type(str(input_path))
        ext = input_path.suffix.lower()
        for backend in self.backends:
            if backend.can_handle(mime or "", ext):
                result = await backend.ingest(input_path)
                out_path.write_text(result.normalized, encoding="utf-8")
                return result
        raise IngestUnsupported(f"no backend for mime={mime} ext={ext}")
