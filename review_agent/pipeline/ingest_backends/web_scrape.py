"""WebScrapBackend — fetch + clean a web page into markdown for review material.

Called DIRECTLY by dispatcher when URLs are detected in a Requester message
(NOT routed through IngestPipeline.can_handle, which is mime/ext based and
URLs don't fit that model).
"""
from __future__ import annotations

import re
from pathlib import Path

from .base import IngestBackend, IngestRejected, IngestResult


class WebScrapBackend(IngestBackend):
    name = "web_scrape"
    kind = "text"

    def can_handle(self, mime: str, ext: str) -> bool:
        # invoked directly by dispatcher; we still accept .url files defensively
        return ext.lower() == ".url" or mime == "text/x-uri"

    async def ingest(self, input_path: Path) -> IngestResult:
        """Rare path: input_path holds URLs one per line (.url file)."""
        raw = input_path.read_text(encoding="utf-8", errors="replace").strip()
        urls = _extract_urls(raw)
        if not urls:
            raise IngestRejected("没在内容里找到 URL，请贴正文给我。")
        return await self.scrape_urls(urls)

    async def scrape_urls(self, urls: list[str]) -> IngestResult:
        """Public API: dispatcher hands a list of URLs directly."""
        if not urls:
            raise IngestRejected("URL 列表是空的。")

        results: list[str] = []
        for url in urls:
            try:
                text = await self._scrape_one(url)
                if text:
                    results.append(f"## {url}\n\n{text}")
            except Exception as e:  # noqa: BLE001 (per-URL failure tolerated)
                results.append(f"## {url}\n\n> ⚠ 抓取失败: {e}")

        if not results or all("抓取失败" in r for r in results):
            raise IngestRejected(
                "所有 URL 都抓不到内容（可能被反爬 / 需要登录 / 网络不通）。"
                "请直接贴正文给我。"
            )
        combined = "\n\n---\n\n".join(results)
        return IngestResult(
            backend="web_scrape",
            normalized=f"[🌐 已从 {len(urls)} 个网页抓取内容]\n\n{combined}",
            note=f"scraped {len(urls)} URLs, {len(combined)} chars",
        )

    async def _scrape_one(self, url: str) -> str:
        import httpx
        headers = {
            "User-Agent": (
                "review-agent/3.1 (bot; review purposes only; "
                "contact admin for questions)"
            ),
        }
        async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                       headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        # Try readability-lxml first
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

        # Fallback: bs4 plain-text extraction (B7 fix: handle import error)
        try:
            from bs4 import BeautifulSoup  # type: ignore
        except ImportError as e:
            raise IngestRejected(
                f"网页抓取依赖没装（{e}）。让 admin 跑 "
                "`pip install -e \".[multimodal]\"` 装 readability-lxml + beautifulsoup4。"
            ) from None

        soup = BeautifulSoup(html, "lxml")
        title = soup.title.string if soup.title else ""
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        body = soup.find("body")
        text = body.get_text(separator="\n", strip=True) if body else html
        text = re.sub(r"\n{3,}", "\n\n", text)
        if not title and text[:80]:
            title = text[:80].split("\n")[0]
        return f"### {title}\n\n{text}"


def _extract_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s<>\"')\]]+", text)
    cleaned = [u.rstrip(".,;:!?）)") for u in urls]
    seen, out = set(), []
    for u in cleaned:
        if u not in seen:
            seen.add(u); out.append(u)
    return out


def _html_to_markdown(html: str) -> str:
    try:
        from markdownify import markdownify as md  # type: ignore
        return md(html, heading_style="ATX", strip=["img", "video"])
    except ImportError:
        return re.sub(r"<[^>]+>", "", html)
