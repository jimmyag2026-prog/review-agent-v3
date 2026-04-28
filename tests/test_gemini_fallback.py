"""Gemini fallback paths in ImageBackend + AudioBackend (mocked httpx)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from review_agent.pipeline.ingest_backends.audio import AudioBackend
from review_agent.pipeline.ingest_backends.base import IngestRejected
from review_agent.pipeline.ingest_backends.image import ImageBackend


def _png_bytes() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


def _ogg_bytes() -> bytes:
    return b"OggS" + b"\x00" * 200


def _mock_response(status: int = 200, json_body: dict | None = None):
    resp = MagicMock()
    resp.status_code = status
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_body or {})
    return resp


@pytest.mark.asyncio
async def test_image_uses_gemini_when_key_set_and_tesseract_missing(tmp_path, monkeypatch):
    img = tmp_path / "x.png"
    img.write_bytes(_png_bytes())
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    fake_resp = _mock_response(200, {
        "candidates": [{"content": {"parts": [{"text": "extracted text from image"}]}}],
    })
    with patch("shutil.which", return_value=None), \
         patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.post = AsyncMock(return_value=fake_resp)
        result = await ImageBackend().ingest(img)

    assert result.backend == "gemini-vision"
    assert "extracted text from image" in result.normalized
    posted_url = instance.post.call_args.args[0]
    assert "generativelanguage.googleapis.com" in posted_url
    assert "gemini-2.5-flash" in posted_url


@pytest.mark.asyncio
async def test_image_gemini_model_override(tmp_path, monkeypatch):
    img = tmp_path / "x.png"
    img.write_bytes(_png_bytes())
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setenv("REVIEW_AGENT_GEMINI_MODEL", "gemini-3-pro-preview")

    fake_resp = _mock_response(200, {
        "candidates": [{"content": {"parts": [{"text": "x"}]}}],
    })
    with patch("shutil.which", return_value=None), \
         patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.post = AsyncMock(return_value=fake_resp)
        await ImageBackend().ingest(img)

    posted_url = instance.post.call_args.args[0]
    assert "gemini-3-pro-preview" in posted_url


@pytest.mark.asyncio
async def test_image_prefers_gemini_over_openai(tmp_path, monkeypatch):
    """Gemini ordering: local tesseract → Gemini → OpenAI. Both keys set: Gemini wins."""
    img = tmp_path / "x.png"
    img.write_bytes(_png_bytes())
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")

    fake_resp = _mock_response(200, {
        "candidates": [{"content": {"parts": [{"text": "from gemini"}]}}],
    })
    with patch("shutil.which", return_value=None), \
         patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.post = AsyncMock(return_value=fake_resp)
        result = await ImageBackend().ingest(img)

    assert result.backend == "gemini-vision"
    assert "from gemini" in result.normalized
    # OpenAI URL should NOT have been called
    posted_url = instance.post.call_args.args[0]
    assert "api.openai.com" not in posted_url


@pytest.mark.asyncio
async def test_image_falls_back_to_openai_when_only_openai_set(tmp_path, monkeypatch):
    img = tmp_path / "x.png"
    img.write_bytes(_png_bytes())
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")

    fake_resp = _mock_response(200, {
        "choices": [{"message": {"content": "from openai"}}],
    })
    with patch("shutil.which", return_value=None), \
         patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.post = AsyncMock(return_value=fake_resp)
        result = await ImageBackend().ingest(img)

    assert result.backend == "openai-vision"


@pytest.mark.asyncio
async def test_image_no_fallback_raises_with_helpful_message(tmp_path, monkeypatch):
    img = tmp_path / "x.png"
    img.write_bytes(_png_bytes())
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with patch("shutil.which", return_value=None):
        with pytest.raises(IngestRejected, match="GEMINI_API_KEY"):
            await ImageBackend().ingest(img)


@pytest.mark.asyncio
async def test_audio_uses_gemini_when_only_key_is_gemini(tmp_path, monkeypatch):
    audio = tmp_path / "v.ogg"
    audio.write_bytes(_ogg_bytes())
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    fake_resp = _mock_response(200, {
        "candidates": [{"content": {"parts": [{"text": "transcribed audio content"}]}}],
    })
    with patch("shutil.which", return_value=None), \
         patch.dict("sys.modules", {"whisper": None}, clear=False), \
         patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.post = AsyncMock(return_value=fake_resp)
        result = await AudioBackend().ingest(audio)

    assert result.backend == "gemini-audio"
    assert "transcribed audio content" in result.normalized
    posted_url = instance.post.call_args.args[0]
    assert "generativelanguage.googleapis.com" in posted_url


@pytest.mark.asyncio
async def test_audio_no_speech_detected_raises(tmp_path, monkeypatch):
    audio = tmp_path / "v.ogg"
    audio.write_bytes(_ogg_bytes())
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    fake_resp = _mock_response(200, {
        "candidates": [{"content": {"parts": [{"text": "[no speech detected]"}]}}],
    })
    with patch("shutil.which", return_value=None), \
         patch.dict("sys.modules", {"whisper": None}, clear=False), \
         patch("httpx.AsyncClient") as MockClient:
        instance = MockClient.return_value.__aenter__.return_value
        instance.post = AsyncMock(return_value=fake_resp)
        with pytest.raises(IngestRejected, match="没识别到"):
            await AudioBackend().ingest(audio)
