"""LarkDocBackend — fetch Lark Doc / Wiki content via Lark Open API.

Called DIRECTLY by dispatcher when Lark URLs are detected in a Requester
message. Goes through the bot's tenant_access_token (which the user already
has), so works on docs the bot has access to without needing OAuth flow.

URL patterns:
- https://<tenant>.feishu.cn/docx/<doc_id>
- https://<tenant>.feishu.cn/wiki/<wiki_token>
- Same on .larksuite.com (international)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from .base import IngestBackend, IngestRejected, IngestResult

if TYPE_CHECKING:
    from ...lark.client import LarkClient


_LARK_URL_RE = re.compile(
    r"https?://[^/\s]+\.(?:feishu\.cn|larksuite\.com)"
    r"/(?P<kind>docx|docs|wiki)/(?P<token>[A-Za-z0-9_-]+)"
)


def extract_lark_urls(text: str) -> list[tuple[str, str, str]]:
    """Find all Lark URLs in text. Returns list of (url, kind, token)."""
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for m in _LARK_URL_RE.finditer(text):
        url = m.group(0)
        if url in seen:
            continue
        seen.add(url)
        out.append((url, m.group("kind"), m.group("token")))
    return out


class LarkDocBackend(IngestBackend):
    name = "lark_doc"
    kind = "text"

    def __init__(self, lark_client: "LarkClient | None" = None):
        self.lark = lark_client

    def can_handle(self, mime: str, ext: str) -> bool:
        return False  # invoked directly by dispatcher, never via mime/ext

    async def ingest(self, input_path: Path) -> IngestResult:  # pragma: no cover
        raise IngestRejected("LarkDocBackend 只能通过 fetch_lark_urls() 调用")

    async def fetch_lark_urls(
        self, urls: list[tuple[str, str, str]],
    ) -> IngestResult:
        """Fetch Lark Doc / Wiki content. urls is list of (url, kind, token)."""
        if self.lark is None:
            raise IngestRejected(
                "LarkDocBackend 没有 lark_client 注入。这是配置 bug，让 admin 看一下。"
            )
        if not urls:
            raise IngestRejected("URL 列表是空的。")

        results: list[str] = []
        for url, kind, token in urls:
            try:
                text = await self._fetch_one(kind, token)
                if text:
                    results.append(f"## {url}\n\n{text}")
                else:
                    results.append(f"## {url}\n\n> ⚠ 文档为空")
            except Exception as e:  # noqa: BLE001
                results.append(
                    f"## {url}\n\n> ⚠ 无法读取（可能 bot 没权限或链接失效）：{e}"
                )

        if all("⚠" in r for r in results):
            raise IngestRejected(
                "Lark 链接全部读取失败 — 检查 bot 是否被加入了文档协作者。"
                "（添加方式：飞书文档右上「分享」→ 添加应用 → 搜你的 bot）"
            )

        combined = "\n\n---\n\n".join(results)
        return IngestResult(
            backend="lark_doc",
            normalized=f"[📄 已从 {len(urls)} 个 Lark 文档读取内容]\n\n{combined}",
            note=f"fetched {len(urls)} Lark URLs, {len(combined)} chars",
        )

    async def _fetch_one(self, kind: str, token: str) -> str:
        if kind in ("docx", "docs"):
            return await self.lark.get_doc_raw(token)
        if kind == "wiki":
            node = await self.lark.get_wiki_node(token)
            doc_id = node.get("obj_token") or node.get("document_id")
            if not doc_id:
                raise IngestRejected(f"wiki 节点 {token} 没有关联 doc_id")
            return await self.lark.get_doc_raw(doc_id)
        raise IngestRejected(f"未知 Lark URL 类型: {kind}")
