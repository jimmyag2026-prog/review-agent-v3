"""YouTubeBackend — extract transcripts from YouTube videos as review input.

Called DIRECTLY by dispatcher when YouTube URLs are detected in a Requester
message (not through IngestPipeline mime/ext matching).

Supports:
- youtube.com/watch?v=<id>
- youtu.be/<id>
- youtube.com/shorts/<id>

Primary: youtube-transcript-api Python package
Fallback: youtubetranscript.com public service
"""
from __future__ import annotations

import re
from pathlib import Path

from .base import IngestBackend, IngestRejected, IngestResult


_YT_URL_RE = re.compile(
    r"(?:https?://)?"
    r"(?:www\.)?"
    r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/|v/)|youtu\.be/)"
    r"(?P<video_id>[A-Za-z0-9_-]{11})"
)


def extract_youtube_urls(text: str) -> list[tuple[str, str]]:
    """Find YouTube URLs in text. Returns list of (url, video_id)."""
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _YT_URL_RE.finditer(text):
        vid = m.group("video_id")
        if vid in seen:
            continue
        seen.add(vid)
        result.append((m.group(0), vid))
    return result


class YouTubeBackend(IngestBackend):
    name = "youtube"
    kind = "text"

    def can_handle(self, mime: str, ext: str) -> bool:
        return False  # invoked directly by dispatcher

    async def ingest(self, input_path: Path) -> IngestResult:  # pragma: no cover
        raise IngestRejected("YouTubeBackend 只能通过 fetch_urls() 调用")

    async def fetch_urls(
        self, urls: list[tuple[str, str]],
    ) -> IngestResult:
        """Fetch transcripts. urls is list of (url, video_id)."""
        if not urls:
            raise IngestRejected("URL 列表是空的。")

        results: list[str] = []
        for url, vid in urls:
            try:
                text = await self._transcribe_one(vid)
                if text:
                    results.append(f"## {url}\n\n{text}")
                else:
                    results.append(f"## {url}\n\n> ⚠ 没有可用的字幕/转录")
            except Exception as e:
                results.append(f"## {url}\n\n> ⚠ 转录失败：{e}")

        if all("⚠" in r for r in results):
            raise IngestRejected(
                "所有 YouTube 视频都无法获取转录（可能没有字幕、区域限制、或视频不存在）。"
            )

        combined = "\n\n---\n\n".join(results)
        return IngestResult(
            backend="youtube",
            normalized=f"[🎬 已从 {len(urls)} 个 YouTube 视频提取字幕]\n\n{combined}",
            note=f"transcribed {len(urls)} videos, {len(combined)} chars",
        )

    async def _transcribe_one(self, video_id: str) -> str | None:
        # Attempt 1: youtube-transcript-api (pure Python, no key needed)
        try:
            import httpx
            # youtube-transcript-api uses a REST endpoint
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"https://youtubetranscript.com/?v={video_id}&format=txt",
                    headers={"User-Agent": "review-agent/3.1"},
                )
                if resp.status_code == 200 and resp.text.strip():
                    return resp.text.strip()
        except Exception:
            pass

        # Attempt 2: try the Python library if installed
        try:
            from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
            transcript = YouTubeTranscriptApi.get_transcript(
                video_id, languages=["zh-Hans", "zh", "en"],
            )
            if transcript:
                return " ".join(seg["text"] for seg in transcript)
        except ImportError:
            pass
        except Exception:
            pass

        # Attempt 3: try auto-generated captions via innertube
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15) as client:
                # Fetch the video page to get caption tracks
                resp = await client.get(
                    f"https://www.youtube.com/watch?v={video_id}",
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36"
                        ),
                    },
                )
                html = resp.text
                # Try to extract caption URL from ytInitialPlayerResponse
                import json
                match = re.search(
                    r'ytInitialPlayerResponse\s*=\s*({.*?});',
                    html,
                    re.DOTALL,
                )
                if match:
                    player = json.loads(match.group(1))
                    captions = (
                        player.get("captions", {})
                        .get("playerCaptionsTracklistRenderer", {})
                        .get("captionTracks", [])
                    )
                    if captions:
                        base_url = captions[0].get("baseUrl", "")
                        if base_url:
                            caption_resp = await client.get(base_url)
                            if caption_resp.status_code == 200:
                                # Parse XML captions
                                import xml.etree.ElementTree as ET
                                root = ET.fromstring(caption_resp.content)
                                texts = []
                                for text_elem in root.iter("{http://www.w3.org/2005/Atom}text"):
                                    if text_elem.text:
                                        texts.append(text_elem.text.strip())
                                # Also check without namespace
                                for text_elem in root.iter("text"):
                                    if text.elem.text:
                                        texts.append(text_elem.text.strip())
                                if texts:
                                    return " ".join(texts)
        except Exception:
            pass

        return None
