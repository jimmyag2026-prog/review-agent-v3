"""Detect file format from raw bytes (magic numbers).

Used by dispatcher to pick the right extension when Lark hands us
binary blobs without a reliable filename hint.
"""
from __future__ import annotations


def detect_image_ext(raw: bytes) -> str:
    """Return canonical image extension (with leading dot) or '.bin' if unknown."""
    if len(raw) < 8:
        return ".bin"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if raw[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if raw[:4] == b"GIF8":
        return ".gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    if raw[:2] == b"BM":
        return ".bmp"
    if raw[:4] in (b"II*\x00", b"MM\x00*"):
        return ".tiff"
    return ".bin"


def detect_audio_ext(raw: bytes) -> str:
    """Return canonical audio extension or '.bin' if unknown."""
    if len(raw) < 12:
        return ".bin"
    if raw[:4] == b"OggS":
        return ".ogg"
    if raw[:3] == b"ID3" or (raw[:2] == b"\xff\xfb") or (raw[:2] == b"\xff\xf3"):
        return ".mp3"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        return ".wav"
    if raw[4:8] == b"ftyp":
        # MP4/M4A/AAC family
        return ".m4a"
    if raw[:4] == b"fLaC":
        return ".flac"
    return ".bin"


def detect_file_ext(raw: bytes) -> str:
    """Generic file: try image first, then audio, then sniff a few common docs."""
    img = detect_image_ext(raw)
    if img != ".bin":
        return img
    aud = detect_audio_ext(raw)
    if aud != ".bin":
        return aud
    if raw[:4] == b"%PDF":
        return ".pdf"
    if raw[:2] == b"PK":
        # zip-based: docx/xlsx/pptx — leave as .zip-ish, downstream will reject if unsupported
        return ".zip"
    if raw[:5] == b"{\\rtf":
        return ".rtf"
    return ".bin"
