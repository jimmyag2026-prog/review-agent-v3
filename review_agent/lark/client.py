from __future__ import annotations

import json
from typing import Any

import httpx

from ..util import log
from .token import TenantTokenCache

_logger = log.get(__name__)


class LarkClient:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        *,
        base_url: str = "https://open.feishu.cn",
        http: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._http = http or httpx.AsyncClient(timeout=30.0)
        self._owned = http is None
        self._token = TenantTokenCache(app_id, app_secret, self.base_url, self._http)

    async def aclose(self) -> None:
        if self._owned:
            await self._http.aclose()

    async def _post(self, path: str, payload: dict) -> dict:
        token = await self._token.get()
        r = await self._http.post(
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
        )
        r.raise_for_status()
        return r.json()

    async def _get(self, path: str, params: dict | None = None) -> dict:
        token = await self._token.get()
        r = await self._http.get(
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        r.raise_for_status()
        return r.json()

    async def send_dm_text(self, open_id: str, text: str) -> str:
        body = {
            "receive_id": open_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        out = await self._post("/open-apis/im/v1/messages?receive_id_type=open_id", body)
        return out.get("data", {}).get("message_id", "")

    async def send_dm_post(self, open_id: str, post_paragraphs: list[list[dict]],
                           *, title: str = "") -> str:
        """Send a Lark `post` (rich text) DM. `post_paragraphs` is a list of paragraphs,
        each paragraph being a list of element dicts (e.g. {"tag":"text","text":"...",
        "style":["bold"]}). See https://open.feishu.cn/document/server-docs/im-v1/message-content-description/create_json
        """
        content = json.dumps(
            {"zh_cn": {"title": title, "content": post_paragraphs}},
            ensure_ascii=False,
        )
        body = {"receive_id": open_id, "msg_type": "post", "content": content}
        out = await self._post("/open-apis/im/v1/messages?receive_id_type=open_id", body)
        return out.get("data", {}).get("message_id", "")

    async def get_user(self, open_id: str) -> dict[str, Any]:
        out = await self._get(
            f"/open-apis/contact/v3/users/{open_id}",
            params={"user_id_type": "open_id"},
        )
        return out.get("data", {}).get("user", {})

    async def download_file(self, message_id: str, file_key: str, *, kind: str = "file") -> bytes:
        token = await self._token.get()
        path = f"/open-apis/im/v1/messages/{message_id}/resources/{file_key}"
        r = await self._http.get(
            f"{self.base_url}{path}",
            params={"type": kind},
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        return r.content

    # ── Multimodal I/O extensions (Phase 7) ───────────────

    async def get_doc_raw(self, document_id: str, *, lang: int = 0) -> str:
        """Fetch the plain-text content of a Lark Doc.

        https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/raw_content
        """
        out = await self._get(
            f"/open-apis/docx/v1/documents/{document_id}/raw_content",
            params={"lang": lang},
        )
        content_data = out.get("data", {}).get("content", "")
        return content_data if isinstance(content_data, str) else str(content_data)

    async def get_wiki_node(self, wiki_token: str, *,
                            obj_type: str = "docx") -> dict:
        """Get wiki node metadata (title, document_id, obj_type).

        https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/get_node
        """
        out = await self._get(
            f"/open-apis/wiki/v2/spaces/get_node",
            params={"token": wiki_token, "obj_type": obj_type},
        )
        node = out.get("data", {}).get("node", {})
        return node

    async def download_attachment(
        self, message_id: str, file_key: str,
        *, kind: str = "file",
    ) -> tuple[bytes, str, str]:
        """Download a Lark message attachment. Returns (raw_bytes, filename, mime_type).

        kind: "file" | "image" | "audio"
        """
        # The download_file already handles all attachment types
        raw = await self.download_file(message_id, file_key, kind=kind)
        # Try to infer filename from Lark; fallback to file_key
        # (Lark doesn't expose filename in the download headers directly,
        #  the dispatcher should pass it from the event payload)
        return raw, file_key, _mime_for_kind(kind)

    async def append_doc_blocks(
        self, document_id: str, blocks: list[dict],
    ) -> dict:
        """Append blocks to an existing Lark Doc.

        Blocks follow the Docx block schema:
        https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block-children/create

        Example block:
            {"block_type": 2, "text": {"elements": [{"text_run": {"content": "Hello"}}]}}
        """
        out = await self._post(
            f"/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children",
            {"children": blocks, "index": -1},  # -1 = append to end
        )
        return out.get("data", {})

    async def create_doc(self, title: str, body_text: str) -> dict:
        """Create a Lark doc and seed it with a single text block."""
        meta = await self._post("/open-apis/docx/v1/documents", {"title": title})
        doc = meta.get("data", {}).get("document", {})
        doc_id = doc.get("document_id")
        if doc_id:
            try:
                await self._post(
                    f"/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                    {
                        "children": [
                            {
                                "block_type": 2,
                                "text": {"elements": [{"text_run": {"content": body_text}}]},
                            }
                        ]
                    },
                )
            except httpx.HTTPError as e:
                _logger.warning("seed doc body failed (doc still created): %s", e)
        return doc


def _mime_for_kind(kind: str) -> str:
    """Map Lark attachment 'kind' to a MIME type for file extension guessing."""
    return {
        "image": "image/jpeg",
        "audio": "audio/mpeg",
        "file": "application/octet-stream",
    }.get(kind, "application/octet-stream")
