"""Magic-bytes detection used by dispatcher to assign correct extension."""
from review_agent.util.file_magic import (
    detect_audio_ext,
    detect_file_ext,
    detect_image_ext,
)


def test_image_png():
    assert detect_image_ext(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100) == ".png"


def test_image_jpeg():
    assert detect_image_ext(b"\xff\xd8\xff\xe0" + b"\x00" * 100) == ".jpg"


def test_image_gif():
    assert detect_image_ext(b"GIF89a" + b"\x00" * 100) == ".gif"
    assert detect_image_ext(b"GIF87a" + b"\x00" * 100) == ".gif"


def test_image_webp():
    assert detect_image_ext(b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100) == ".webp"


def test_image_bmp():
    assert detect_image_ext(b"BM" + b"\x00" * 100) == ".bmp"


def test_image_unknown():
    assert detect_image_ext(b"random text bytes here") == ".bin"
    assert detect_image_ext(b"") == ".bin"
    assert detect_image_ext(b"abc") == ".bin"


def test_audio_ogg():
    assert detect_audio_ext(b"OggS\x00\x00\x00\x00\x00\x00\x00\x00") == ".ogg"


def test_audio_mp3():
    assert detect_audio_ext(b"ID3" + b"\x00" * 100) == ".mp3"
    assert detect_audio_ext(b"\xff\xfb" + b"\x00" * 100) == ".mp3"


def test_audio_wav():
    assert detect_audio_ext(b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * 100) == ".wav"


def test_audio_m4a():
    # MP4-family files have `ftyp` at offset 4
    assert detect_audio_ext(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 100) == ".m4a"


def test_audio_flac():
    assert detect_audio_ext(b"fLaC" + b"\x00" * 100) == ".flac"


def test_audio_unknown():
    assert detect_audio_ext(b"unknown") == ".bin"
    assert detect_audio_ext(b"") == ".bin"


def test_file_pdf():
    assert detect_file_ext(b"%PDF-1.4\n" + b"\x00" * 100) == ".pdf"


def test_file_zip_family():
    # docx / xlsx / pptx all start with PK
    assert detect_file_ext(b"PK\x03\x04" + b"\x00" * 100) == ".zip"


def test_file_falls_through_to_image():
    # detect_file_ext checks image first
    assert detect_file_ext(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100) == ".png"
