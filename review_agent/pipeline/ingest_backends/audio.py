"""AudioBackend — transcribe voice via whisper.cpp (primary) or OpenAI Whisper API (fallback)."""
from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from .base import IngestBackend, IngestRejected, IngestResult


class AudioBackend(IngestBackend):
    name = "audio"
    kind = "audio"

    _AUDIO_MIMES = {
        "audio/mpeg", "audio/mp3", "audio/mp4", "audio/wav",
        "audio/ogg", "audio/webm", "audio/x-m4a", "audio/aac",
        "audio/flac", "audio/opus",
    }
    _AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".webm", ".opus"}

    def can_handle(self, mime: str, ext: str) -> bool:
        return mime in self._AUDIO_MIMES or ext.lower() in self._AUDIO_EXTS

    async def ingest(self, input_path: Path) -> IngestResult:
        self.validate_size(input_path.stat().st_size)

        # ── 1. local whisper.cpp (free, no network) ──
        if shutil.which("whisper-cpp") or shutil.which("whisper.cpp"):
            text = await self._run_whisper_cpp(input_path)
            if text:
                return IngestResult(
                    backend="whisper-cpp",
                    normalized=_prepend_meta(text, input_path),
                    note=f"whisper.cpp: {len(text)} chars",
                )

        # ── 2. local openai-whisper python package ──
        try:
            import whisper  # type: ignore  # noqa: F401
            return await self._local_whisper(input_path)
        except ImportError:
            pass

        # ── 3. Gemini audio API (free tier supports gemini-2.5-flash) ──
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if gemini_key:
            return await self._gemini_audio(input_path, gemini_key)

        # ── 4. OpenAI Whisper API ──
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if openai_key:
            return await self._api_whisper(input_path, openai_key)

        raise IngestRejected(
            "语音转文字没装（whisper-cpp / openai-whisper / GEMINI_API_KEY / OPENAI_API_KEY 都没有）。"
            "让 admin 跑 `review-agent install-multimodal` 一键装本地 whisper，"
            "或在 secrets.env 里填 GEMINI_API_KEY 或 OPENAI_API_KEY。"
            "当前你直接贴文字给我也行。"
        )

    async def _run_whisper_cpp(self, input_path: Path) -> str:
        """B3 fix: don't use --output-txt (writes to file). Use -nt + stdout."""
        bin_name = "whisper-cpp" if shutil.which("whisper-cpp") else "whisper.cpp"
        proc = await asyncio.create_subprocess_exec(
            bin_name,
            "-m", self._whisper_model_path(),
            "-f", str(input_path),
            "--language", "auto",  # B6: don't hardcode zh
            "-nt",                  # no timestamps
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return ""
        # whisper.cpp prints transcription on stdout, one line per segment
        # filter out timestamp-bracket lines defensively
        lines = [
            line.strip() for line in stdout.decode("utf-8", "replace").splitlines()
            if line.strip() and not line.strip().startswith("[")
        ]
        return "\n".join(lines).strip()

    async def _local_whisper(self, input_path: Path) -> IngestResult:
        """B5 fix: load_model + transcribe are blocking; run in thread."""
        import whisper  # type: ignore

        def _sync_transcribe() -> str:
            model = whisper.load_model("base")
            result = model.transcribe(str(input_path))  # B6: language auto-detect
            return (result.get("text") or "").strip()

        text = await asyncio.to_thread(_sync_transcribe)
        if not text:
            raise IngestRejected("语音转文字返回空。试试重发或换格式（OGG/M4A/MP3）。")
        return IngestResult(
            backend="whisper-local",
            normalized=_prepend_meta(text, input_path),
            note=f"whisper-local base: {len(text)} chars",
        )

    async def _gemini_audio(self, input_path: Path, api_key: str) -> IngestResult:
        """Gemini supports audio understanding via inline_data. Prompt for verbatim transcription."""
        import base64
        import httpx
        model = os.environ.get("REVIEW_AGENT_GEMINI_MODEL", "gemini-2.5-flash")
        mime = _guess_mime(input_path)
        data_b64 = base64.b64encode(input_path.read_bytes()).decode()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        prompt = (
            "Transcribe this audio verbatim. Return ONLY the transcribed text "
            "(no preamble, no labels, no language note). If the audio is silent "
            "or contains no speech, reply with exactly: [no speech detected]"
        )
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, json={
                "contents": [{
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": mime, "data": data_b64}},
                    ],
                }],
                "generationConfig": {"maxOutputTokens": 4096},
            })
            resp.raise_for_status()
            body = resp.json()
            text = ""
            try:
                text = body["candidates"][0]["content"]["parts"][0]["text"].strip()
            except (KeyError, IndexError):
                pass

        if not text or text == "[no speech detected]":
            raise IngestRejected("Gemini 没识别到语音内容。可能太短或全静音。")
        return IngestResult(
            backend="gemini-audio",
            normalized=_prepend_meta(text, input_path),
            note=f"audio {model}: {len(text)} chars",
        )

    async def _api_whisper(self, input_path: Path, api_key: str) -> IngestResult:
        import httpx
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (input_path.name, input_path.read_bytes(),
                                _guess_mime(input_path))},
                data={"model": "whisper-1"},  # B6: no language= → auto-detect
            )
            resp.raise_for_status()
            text = (resp.json().get("text") or "").strip()

        if not text:
            raise IngestRejected("Whisper API 返回空。语音可能太短或全静音。")
        return IngestResult(
            backend="whisper-api",
            normalized=_prepend_meta(text, input_path),
            note=f"whisper-1 API: {len(text)} chars",
        )

    @staticmethod
    def _whisper_model_path() -> str:
        candidates = [
            os.path.expanduser("~/.whisper/models/ggml-base.bin"),
            os.path.expanduser("~/.cache/whisper.cpp/ggml-base.bin"),
            "/usr/local/share/whisper/models/ggml-base.bin",
            "/opt/whisper.cpp/models/ggml-base.bin",
        ]
        for c in candidates:
            if os.path.exists(c):
                return c
        return "ggml-base.bin"


def _prepend_meta(text: str, input_path: Path) -> str:
    return f"[🎤 语音消息已转文字: *{input_path.stem}*]\n\n{text}"


def _guess_mime(path: Path) -> str:
    return {
        ".mp3": "audio/mpeg", ".wav": "audio/wav",
        ".ogg": "audio/ogg", ".opus": "audio/ogg",
        ".m4a": "audio/x-m4a", ".aac": "audio/aac",
        ".flac": "audio/flac", ".webm": "audio/webm",
    }.get(path.suffix.lower(), "audio/mpeg")
