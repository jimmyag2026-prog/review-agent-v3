from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import urlparse

from .base import IngestBackend, IngestRejected, IngestResult


class WebScrapBackend(IngestBackend):
    """Extract readable content from external web pages (Notion, Confluence, blogs, etc.)."""

    name = "web_scrape"
    kind = "text"  # output is text, even though input is URL

    def can_handle(self, mime: str, ext: str) -> bool:
        # This backend is triggered by URL detection in the dispatcher,
        # NOT by file mime/ext. But we still implement can_handle for
        # completeness: if someone feeds a .url file we can process it.
        return ext.lower() == ".url" or mime == "text/x-uri"

    async def ingest(self, input_path: Path) -> IngestResult:
        """Read URLs from input_path (one per line) and scrape each."""
        raw = input_path.read_text(encoding="utf-8", errors="replace").strip()
        urls = _extract_urls(raw)

        if not urls:
            raise IngestRejected(
                "没有在内容里发现可用的 URL。请确认链接格式正确。"
            )

        results: list[str] = []
        for url in urls:
            try:
                text = await self._scrape_one(url)
                if text:
                    results.append(f"## {url}\n\n{text}")
            except Exception as e:
                results.append(f"## {url}\n\n> ⚠ 抓取失败: {e}")

        if not results or all("抓取失败" in r for r in results):
            raise IngestRejected(
                "所有 URL 都无法抓取。可能被反爬、需要登录、或网络不通。"
                "请直接贴正文给我。"
            )

        combined = "\n\n---\n\n".join(results)
        return IngestResult(
            backend="web_scrape",
            normalized=(
                f"[🌐 已从 {len(urls)} 个网页抓取内容]\n\n"
                f"{combined}"
            ),
            note=f"scraped {len(urls)} URLs, {len(combined)} total chars",
        )

    async def _scrape_one(self, url: str) -> str:
        # Try readability-lxml (best for articles) → bs4 fallback
        import httpx

        headers = {
            "User-Agent": (
                "review-agent/3.0 (bot; review purposes only; "
                "contact admin for questions)"
            ),
        }

        async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
            base_url = str(resp.url)  # final after redirects

        # ── Primary: readability-lxml ──
        try:
            from readability import Document  # type: ignore
            doc = Document(html)
            title = doc.title() or ""
            body_html = doc.summary()
            md = _html_to_markdown(body_html)
            if md.strip():
                return f"### {title}\n\n{md}"
        except ImportError:
            pass

        # ── Fallback: bs4 body text extraction ──
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        title = soup.title.string if soup.title else ""
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        body = soup.find("body")
        text = body.get_text(separator="\n", strip=True) if body else html
        # compact whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        if not title and text[:80]:
            title = text[:80].split("\n")[0]
        return f"### {title}\n\n{text}"


def _extract_urls(text: str) -> list[str]:
    """Pull http/https URLs from text, deduplicate, filter out common noise."""
    urls = re.findall(r"https?://[^\s<>\"')\]]+", text)
    # Clean trailing punctuation
    cleaned = []
    for u in urls:
        u = u.rstrip(".,;:!?）)")
        cleaned.append(u)
    # Deduplicate while preserving order
    seen = set()
    result = []
    for u in cleaned:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


def _html_to_markdown(html: str) -> str:
    """Convert HTML fragment to Markdown using markdownify."""
    try:
        from markdownify import markdownify as md
        return md(html, heading_style="ATX", strip=["img", "video"])
    except ImportError:
        # last resort — strip all tags
        return re.sub(r"<[^>]+>", "", html)
