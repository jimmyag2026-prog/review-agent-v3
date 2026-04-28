from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from .base import IngestBackend, IngestRejected, IngestResult

_EMOJI = chr(0x1F5BC)


class ImageBackend(IngestBackend):
    """OCR image files and screenshots using tesseract (primary) or vision API (fallback)."""

    name = "image"
    kind = "image"

    _IMAGE_MIMES = {
        "image/png", "image/jpeg", "image/jpg",
        "image/webp", "image/bmp", "image/tiff",
    }
    _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}

    def can_handle(self, mime: str, ext: str) -> bool:
        return mime in self._IMAGE_MIMES or ext.lower() in self._IMAGE_EXTS

    async def ingest(self, input_path: Path) -> IngestResult:
        self.validate_size(input_path.stat().st_size)

        # ── primary: tesseract CLI ──
        if shutil.which("tesseract"):
            proc = await asyncio.create_subprocess_exec(
                "tesseract", str(input_path), "stdout",
                "-l", "eng+chi_sim", "--psm", "6",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            text = stdout.decode("utf-8", "replace").strip()
            if text:
                return IngestResult(
                    backend="tesseract",
                    normalized=_prepend_meta(text, input_path),
                    note=f"tesseract (eng+chi_sim, psm=6): {len(text)} chars",
                )

        # ── fallback: vision API ──
        import os
        import base64

        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise IngestRejected(
                "OCR 没装 tesseract，也没有 OPENAI_API_KEY 做 vision 兜底。"
                "请直接贴正文给我。"
            )

        return await self._vision_fallback(input_path, api_key)

    async def _vision_fallback(self, input_path: Path, api_key: str) -> IngestResult:
        # Lazy import to keep httpx optional outside this path
        import httpx
        import base64

        mime = _guess_image_mime(input_path)
        data_url = f"data:{mime};base64,{base64.b64encode(input_path.read_bytes()).decode()}"

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Extract all visible text from this image verbatim. "
                                        "If it's a slide/screenshot/document, preserve the structure "
                                        "(headers, bullets, tables) as markdown. "
                                        "If the image contains diagrams or figures, describe them briefly. "
                                        "Return ONLY the extracted content, no conversational wrapper."
                                    ),
                                },
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        }
                    ],
                    "max_tokens": 4096,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            text = body["choices"][0]["message"]["content"]

        return IngestResult(
            backend="openai-vision",
            normalized=_prepend_meta(text, input_path),
            note=f"vision (gpt-4o-mini): {len(text)} chars",
        )


def _prepend_meta(text: str, input_path: Path) -> str:
    """Attach a 'file was provided' preamble that the reviewer LLM can key on."""
    stem = input_path.stem
    return (
        f"[📎 图片文件已通过 OCR 提取: *{stem}*]\n\n"
        f"{text}"
    )


def _guess_image_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".tif": "image/tiff",
    }.get(suffix, "image/png")
