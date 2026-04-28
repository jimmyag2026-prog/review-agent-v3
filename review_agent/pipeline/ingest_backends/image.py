"""ImageBackend — OCR images via tesseract (primary) or vision API (fallback)."""
from __future__ import annotations

import asyncio
import base64
import os
import shutil
from pathlib import Path

from .base import IngestBackend, IngestRejected, IngestResult


class ImageBackend(IngestBackend):
    name = "image"
    kind = "image"

    _IMAGE_MIMES = {
        "image/png", "image/jpeg", "image/jpg",
        "image/webp", "image/bmp", "image/tiff", "image/gif",
    }
    _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif", ".gif"}

    def can_handle(self, mime: str, ext: str) -> bool:
        return mime in self._IMAGE_MIMES or ext.lower() in self._IMAGE_EXTS

    async def ingest(self, input_path: Path) -> IngestResult:
        self.validate_size(input_path.stat().st_size)

        # ── 1. local tesseract (free, no network) ──
        if shutil.which("tesseract"):
            text = await self._run_tesseract(input_path)
            if text:
                return IngestResult(
                    backend="tesseract",
                    normalized=_prepend_meta(text, input_path),
                    note=f"tesseract eng+chi_sim psm6: {len(text)} chars",
                )

        # ── 2. Gemini vision API (free tier supports gemini-2.5-flash) ──
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if gemini_key:
            return await self._gemini_fallback(input_path, gemini_key)

        # ── 3. OpenAI vision API ──
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if openai_key:
            return await self._openai_fallback(input_path, openai_key)

        raise IngestRejected(
            "OCR 没装 tesseract，也没有 GEMINI_API_KEY / OPENAI_API_KEY 做 vision 兜底。"
            "让 admin 跑 `review-agent install-multimodal` 一键装本地 OCR，"
            "或在 secrets.env 里填 GEMINI_API_KEY 或 OPENAI_API_KEY。"
            "当前你直接贴正文给我也行。"
        )

    async def _run_tesseract(self, input_path: Path) -> str:
        proc = await asyncio.create_subprocess_exec(
            "tesseract", str(input_path), "stdout",
            "-l", "eng+chi_sim", "--psm", "6",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode("utf-8", "replace").strip()

    async def _gemini_fallback(self, input_path: Path, api_key: str) -> IngestResult:
        import httpx
        model = os.environ.get("REVIEW_AGENT_GEMINI_MODEL", "gemini-2.5-flash")
        mime = _guess_image_mime(input_path)
        data_b64 = base64.b64encode(input_path.read_bytes()).decode()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json={
                "contents": [{
                    "parts": [
                        {"text": _VISION_PROMPT},
                        {"inline_data": {"mime_type": mime, "data": data_b64}},
                    ],
                }],
                "generationConfig": {"maxOutputTokens": 4096},
            })
            resp.raise_for_status()
            body = resp.json()
            text = ""
            try:
                text = body["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError):
                pass

        if not text or not text.strip():
            raise IngestRejected("Gemini Vision 没识别到内容。可能是空白图或纯装饰图。")
        return IngestResult(
            backend="gemini-vision",
            normalized=_prepend_meta(text, input_path),
            note=f"vision {model}: {len(text)} chars",
        )

    async def _openai_fallback(self, input_path: Path, api_key: str) -> IngestResult:
        import httpx
        mime = _guess_image_mime(input_path)
        data_url = f"data:{mime};base64,{base64.b64encode(input_path.read_bytes()).decode()}"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}",
                          "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": _VISION_PROMPT},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }],
                    "max_tokens": 4096,
                },
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]

        if not text or not text.strip():
            raise IngestRejected("OpenAI Vision 没识别到内容。可能是空白图或纯装饰图。")
        return IngestResult(
            backend="openai-vision",
            normalized=_prepend_meta(text, input_path),
            note=f"vision gpt-4o-mini: {len(text)} chars",
        )


_VISION_PROMPT = (
    "Extract all visible text from this image verbatim. "
    "If it's a slide/screenshot/document, preserve structure (headers, "
    "bullets, tables) as markdown. If it's a diagram or figure, describe it "
    "briefly. Return ONLY the extracted content, no conversational wrapper."
)


def _prepend_meta(text: str, input_path: Path) -> str:
    return f"[📎 图片已通过 OCR 提取: *{input_path.stem}*]\n\n{text}"


def _guess_image_mime(path: Path) -> str:
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".bmp": "image/bmp", ".tiff": "image/tiff",
        ".tif": "image/tiff", ".gif": "image/gif",
    }.get(path.suffix.lower(), "image/png")
