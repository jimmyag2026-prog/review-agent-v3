from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from .base import IngestBackend, IngestRejected, IngestResult


class PdfBackend(IngestBackend):
    name = "pdf"
    kind = "pdf"

    def can_handle(self, mime: str, ext: str) -> bool:
        return mime == "application/pdf" or ext.lower() == ".pdf"

    async def ingest(self, input_path: Path) -> IngestResult:
        self.validate_size(input_path.stat().st_size)
        # try pdftotext binary
        if shutil.which("pdftotext"):
            proc = await asyncio.create_subprocess_exec(
                "pdftotext", "-layout", str(input_path), "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0 and stdout.strip():
                return IngestResult(backend="pdftotext",
                                    normalized=stdout.decode("utf-8", "replace"))
        # try pdfminer.six
        try:
            from pdfminer.high_level import extract_text  # type: ignore
            text = extract_text(str(input_path))
            if text and text.strip():
                return IngestResult(backend="pdfminer", normalized=text)
        except ImportError:
            pass
        raise IngestRejected(
            "PDF 解析没装（pdftotext / pdfminer.six 都没找到）。"
            "你直接贴正文给我也行。"
        )
