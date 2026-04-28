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
