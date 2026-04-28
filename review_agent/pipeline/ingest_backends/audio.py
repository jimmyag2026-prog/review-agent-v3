from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from .base import IngestBackend, IngestRejected, IngestResult


class AudioBackend(IngestBackend):
    """Transcribe audio files (voice messages) using whisper.cpp or OpenAI Whisper API."""

    name = "audio"
    kind = "audio"

    _AUDIO_MIMES = {
        "audio/mpeg", "audio/mp3", "audio/mp4", "audio/wav",
        "audio/ogg", "audio/webm", "audio/x-m4a", "audio/aac",
        "audio/flac",
    }
    _AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".webm", ".opus"}

    def can_handle(self, mime: str, ext: str) -> bool:
        return mime in self._AUDIO_MIMES or ext.lower() in self._AUDIO_EXTS

    async def ingest(self, input_path: Path) -> IngestResult:
        self.validate_size(input_path.stat().st_size)

        # ── primary: whisper.cpp CLI ──
        if shutil.which("whisper-cpp"):
            # whisper-cpp requires 16kHz mono wav; allow it to auto-convert
            proc = await asyncio.create_subprocess_exec(
                "whisper-cpp",
                "-m", self._whisper_model_path(),
                "-f", str(input_path),
                "--language", "auto",
                "--output-txt",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            text = stdout.decode("utf-8", "replace").strip()
            if proc.returncode == 0 and text:
                return IngestResult(
                    backend="whisper-cpp",
                    normalized=_prepend_meta(text, input_path),
                    note=f"whisper.cpp: {len(text)} chars",
                )

        # ── fallback: openai-whisper Python package ──
        try:
            import whisper  # type: ignore
        except ImportError:
            whisper = None

        if whisper is not None:
            return await self._local_whisper(input_path)
        else:
            import os
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                raise IngestRejected(
                    "语音转文字没装（whisper-cpp / openai-whisper / OPENAI_API_KEY 都没有）。"
                    "请把内容转成文字再发。"
                )
            return await self._api_whisper(input_path, api_key)

    async def _local_whisper(self, input_path: Path) -> IngestResult:
        import whisper  # type: ignore

        model = whisper.load_model("base")
        result = model.transcribe(str(input_path), language="zh")
        text = result.get("text", "").strip()
        if not text:
            raise IngestRejected("语音转文字为空，试试重发或者换个格式。")
        return IngestResult(
            backend="whisper-local",
            normalized=_prepend_meta(text, input_path),
            note=f"whisper-local (base, zh): {len(text)} chars",
        )

    async def _api_whisper(self, input_path: Path, api_key: str) -> IngestResult:
        import httpx

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (input_path.name, input_path.read_bytes(), _guess_mime(input_path))},
                data={"model": "whisper-1", "language": "zh"},
            )
            resp.raise_for_status()
            text = resp.json().get("text", "").strip()

        if not text:
            raise IngestRejected("Whisper API 返回空结果，语音可能太短或静音。")
        return IngestResult(
            backend="whisper-api",
            normalized=_prepend_meta(text, input_path),
            note=f"whisper-1 (API): {len(text)} chars",
        )

    @staticmethod
    def _whisper_model_path() -> str:
        """Look for ggml-base.bin in common locations."""
        import os
        candidates = [
            os.path.expanduser("~/.whisper/models/ggml-base.bin"),
            "/usr/local/share/whisper/models/ggml-base.bin",
            "models/ggml-base.bin",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return "models/ggml-base.bin"  # let whisper-cpp report its own error


def _prepend_meta(text: str, input_path: Path) -> str:
    stem = input_path.stem
    return (
        f"[🎤 语音消息已转文字: *{stem}*]\n\n"
        f"{text}"
    )


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".m4a": "audio/x-m4a",
        ".aac": "audio/aac",
        ".flac": "audio/flac",
        ".webm": "audio/webm",
        ".opus": "audio/ogg",
    }.get(suffix, "audio/mpeg")
