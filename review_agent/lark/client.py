from __future__ import annotations

import asyncio
import json
import random
import time
from typing import Any

import httpx

from ..util import log
from .token import TenantTokenCache

_logger = log.get(__name__)

# ── Retry helpers ───────────────────────────────────────────────

_MAX_ATTEMPTS = 4          # 1 initial + 3 retries = 4 total
_TOKEN_EXPIRED = 99991663  # Lark app-level token-expired code


def _backoff_429(attempt: int, retry_after: str | None) -> float:
    """Return sleep seconds for a 429 rate-limit retry."""
    if retry_after is not None:
        try:
            return float(retry_after)
        except (TypeError, ValueError):
            pass
    return (2 ** attempt) + random.uniform(0, 0.5)


def _backoff_5xx(attempt: int) -> float:
    """Return sleep seconds for a 5xx server-error retry."""
    return (2 ** attempt) * 0.5 + random.uniform(0, 0.5)


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
        self._user_cache: dict[str, tuple[dict[str, Any], float]] = {}  # id → (user, expire_at)
        self._user_cache_ttl: float = 600.0  # ten minutes

    async def aclose(self) -> None:
        if self._owned:
            await self._http.aclose()

    async def _post(self, path: str, payload: dict) -> dict:
        return await self._request_with_retry("POST", path, json=payload)

    async def _get(self, path: str, params: dict | None = None) -> dict:
        return await self._request_with_retry("GET", path, params=params)

    # ── Retry loop (internal) ──────────────────────────────────

    async def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        for attempt in range(_MAX_ATTEMPTS):
            token = await self._token.get()
            headers = {"Authorization": f"Bearer {token}"}
            if json is not None:
                headers["Content-Type"] = "application/json"

            r = await self._http.request(
                method, url, headers=headers, json=json, params=params,
            )
            body = r.json()

            # ── Token expired: invalidate cache, retry ─
            if r.status_code == 200 and body.get("code") == _TOKEN_EXPIRED:
                self._token.invalidate()
                continue

            # ── 2xx (non-token-expired): done ─
            if r.is_success:
                return body

            # ── Non-retryable 4xx: raise immediately ─
            if r.status_code != 429 and 400 <= r.status_code < 500:
                r.raise_for_status()

            # ── 429 / 5xx — retry with backoff ─
            if attempt == _MAX_ATTEMPTS - 1:  # last attempt
                r.raise_for_status()

            if r.status_code == 429:
                delay = _backoff_429(attempt, r.headers.get("Retry-After"))
            else:  # 5xx
                delay = _backoff_5xx(attempt)

            _logger.debug("retry %d/%d for %s %s — sleeping %.2fs",
                          attempt + 1, _MAX_ATTEMPTS - 1, method, path, delay)
            await asyncio.sleep(delay)

        # Should never reach here; last iteration raises above
        raise RuntimeError(f"unreachable: {method} {path}")

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
        """Send a Lark `post` (rich text) DM.

        Wire format (per Lark im/v1/messages spec):
            content = JSON-encoded {"zh_cn": {"title": "...", "content": [[...]]}}
            msg_type = "post"
        DO NOT wrap content in {"post": {...}} — Lark returns 230001
        invalid-message-content if you do, and the rich-text DM is dropped
        (dispatcher then falls back to plain text, hiding the bug).

        See https://open.feishu.cn/document/server-docs/im-v1/message-content-description/create_json
        """
        content = json.dumps(
            {"zh_cn": {"title": title, "content": post_paragraphs}},
            ensure_ascii=False,
        )
        body = {"receive_id": open_id, "msg_type": "post", "content": content}
        out = await self._post("/open-apis/im/v1/messages?receive_id_type=open_id", body)
        return out.get("data", {}).get("message_id", "")

    async def get_user(self, open_id: str) -> dict[str, Any]:
        """Fetch a Lark user by open_id, with 10-minute cache.

        Cache is keyed on open_id. Expired entries are automatically
        re-fetched. API errors are NOT cached — they bubble up immediately.
        """
        now = time.time()
        cached = self._user_cache.get(open_id)
        if cached is not None:
            user, expire_at = cached
            if now < expire_at:
                return user

        out = await self._get(
            f"/open-apis/contact/v3/users/{open_id}",
            params={"user_id_type": "open_id"},
        )
        user = out.get("data", {}).get("user", {})
        self._user_cache[open_id] = (user, now + self._user_cache_ttl)
        return user

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


    # ── Bitable API (Phase 2 — structured input/output) ────────────

    async def get_bitable_records(
        self, app_token: str, table_id: str,
        *, page_size: int = 500, page_token: str | None = None,
        user_id_type: str = "open_id",
    ) -> dict:
        """Fetch records from a Lark Bitable table.

        https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/list
        Returns the full API response dict (data.records / page_token / total).
        """
        params: dict = {"page_size": page_size, "user_id_type": user_id_type}
        if page_token:
            params["page_token"] = page_token
        out = await self._get(
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            params=params,
        )
        return out.get("data", {})

    async def search_bitable_records(
        self, app_token: str, table_id: str,
        field_name: str, operator: str, value: list[str],
        *, page_size: int = 500, page_token: str | None = None,
    ) -> dict:
        """Search records in a Bitable table by field condition.

        https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/search
        operator: "is" | "isNot" | "contains" | "doesNotContain" | "isEmpty" | "isNotEmpty"
        """
        payload: dict = {
            "field_names": [],
            "filter": {
                "conjunction": "and",
                "conditions": [{
                    "field_name": field_name,
                    "operator": operator,
                    "value": value,
                }],
            },
            "page_size": page_size,
            "automatic_fields": True,
        }
        if page_token:
            payload["page_token"] = page_token
        out = await self._post(
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
            payload,
        )
        return out.get("data", {})

    async def create_bitable_record(
        self, app_token: str, table_id: str,
        fields: dict, *, user_id_type: str = "open_id",
    ) -> dict | None:
        """Create a record in a Bitable table.

        fields must match the table schema (field_name → value).
        Returns the created record dict, or None on failure.
        https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/create
        """
        payload = {"fields": fields}
        out = await self._post(
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
            f"?user_id_type={user_id_type}",
            payload,
        )
        return out.get("data", {}).get("record")

    async def list_bitable_tables(
        self, app_token: str, *, page_size: int = 50,
    ) -> list[dict]:
        """List all tables in a Bitable app.

        https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table/list
        Returns list of table dicts with id/name/type/properties/description.
        """
        out = await self._get(
            f"/open-apis/bitable/v1/apps/{app_token}/tables",
            params={"page_size": page_size},
        )
        return out.get("data", {}).get("items", [])

    async def get_bitable_fields(
        self, app_token: str, table_id: str, *,
        page_size: int = 100,
    ) -> list[dict]:
        """List all fields in a Bitable table (schema metadata).

        https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/list
        Returns list of field dicts with field_id/field_name/type/property.
        """
        out = await self._get(
            f"/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            params={"page_size": page_size},
        )
        return out.get("data", {}).get("items", [])

    # ── Lark Sheet API (basic read) ────────────────────────────────

    async def get_sheet_meta(self, spreadsheet_token: str) -> dict:
        """Get sheet metadata (title, sheet count, etc.).

        https://open.feishu.cn/document/server-docs/docs/sheets-v3/spreadsheet-spreadsheet/get_meta
        """
        out = await self._get(
            f"/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets_meta",
        )
        return out.get("data", {})

    async def get_sheet_values(
        self, spreadsheet_token: str, sheet_range: str,
    ) -> list[list[str]]:
        """Read cell values from a sheet range.

        sheet_range format: "<sheet_id>!A1:Z100"
        Returns list of rows, each row is a list of cell values (all strings).
        https://open.feishu.cn/document/server-docs/docs/sheets-v3/data-operation/reading-a-single-range
        """
        import json
        out = await self._get(
            f"/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values/{sheet_range}",
        )
        value_range = out.get("data", {}).get("valueRange", {})
        return value_range.get("values", [])

    # ── YouTube transcript extraction ──────────────────────────────

    async def extract_youtube_transcript(self, video_id: str) -> str | None:
        """Extract transcript from a YouTube video using youtube-transcript-api.

        Returns the full transcript text, or None on failure.
        Requires `pip install youtube-transcript-api` or falls back to fetching
        via httpx (auto-captions on the page).
        """
        try:
            from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore  # noqa: F811
            transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=["zh", "en"])
            if transcript:
                return " ".join(seg["text"] for seg in transcript)
        except ImportError:
            pass
        except Exception:
            pass

        # Fallback: fetch page and extract auto-captions
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"https://youtubetranscript.com/?v={video_id}",
                    headers={"User-Agent": "review-agent/3.1"},
                )
                if resp.status_code == 200:
                    return resp.text
        except Exception:
            pass
        return None


def _mime_for_kind(kind: str) -> str:
    """Map Lark attachment 'kind' to a MIME type for file extension guessing."""
    return {
        "image": "image/jpeg",
        "audio": "audio/mpeg",
        "file": "application/octet-stream",
    }.get(kind, "application/octet-stream")
