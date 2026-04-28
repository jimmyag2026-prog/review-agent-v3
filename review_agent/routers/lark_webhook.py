from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from ..core.storage import Storage
from ..lark import webhook as wh
from ..lark.types import IncomingMessage
from ..tasks.queue import TaskQueue
from ..util import log
from ..util.md import text_hash

router = APIRouter()


def _extract_post_text(parsed: dict) -> str:
    """Pull plain text out of Lark `post` (rich text) message JSON.

    Schema: {"title": "...", "content": [[<element>, ...], [<element>, ...]]}
    Each <element> may have {"tag":"text","text":"..."} / {"tag":"a","text":"..."} /
    {"tag":"at","user_name":"..."} / {"tag":"img"} etc.
    We extract human-readable strings and stitch them by paragraph.
    """
    parts: list[str] = []
    title = parsed.get("title", "")
    if title:
        parts.append(title)
    content = parsed.get("content") or []
    if not isinstance(content, list):
        return "\n".join(parts)
    for paragraph in content:
        if not isinstance(paragraph, list):
            continue
        line: list[str] = []
        for el in paragraph:
            if not isinstance(el, dict):
                continue
            tag = el.get("tag", "")
            if tag in ("text", "a", "code_inline"):
                line.append(el.get("text", ""))
            elif tag == "at":
                line.append(f"@{el.get('user_name') or el.get('user_id', '')}")
            elif tag == "img":
                line.append("[图片]")
            elif tag == "media":
                line.append("[媒体]")
            elif tag == "emotion":
                line.append(el.get("text", ""))
        if line:
            parts.append("".join(line))
    return "\n".join(p for p in parts if p)
_logger = log.get(__name__)


def make_router(storage: Storage, queue: TaskQueue, *, encrypt_key: str, verification_token: str):
    api = APIRouter()

    @api.post("/lark/webhook")
    async def lark_webhook(request: Request):
        raw_body = await request.body()
        try:
            obj = json.loads(raw_body or b"{}")
        except json.JSONDecodeError:
            raise HTTPException(400, "invalid json")

        # 1) signature verify FIRST (raw body bytes; required when encrypt_key is set —
        #    Lark wraps even url_verification in the encrypted envelope, so this must
        #    happen before we can read obj["type"])
        if encrypt_key and request.headers.get("X-Lark-Signature"):
            if not wh.verify_v2_signature(request.headers, raw_body, encrypt_key):
                raise HTTPException(401, "bad signature")

        # 2) decrypt envelope if present
        if "encrypt" in obj:
            if not encrypt_key:
                raise HTTPException(401, "encrypted event but no encrypt_key configured")
            obj = wh.decrypt_aes(obj["encrypt"], encrypt_key)

        # 3) url_verification AFTER decrypt — works for both encrypted and plain modes
        if obj.get("type") == "url_verification":
            return {"challenge": obj.get("challenge", "")}

        if obj.get("token") and verification_token and obj["token"] != verification_token:
            raise HTTPException(401, "bad token")

        header = obj.get("header") or {}
        event_id = header.get("event_id", "")
        if not event_id:
            return {"status": "no_event_id"}
        if storage.event_seen(event_id):
            return {"status": "dup"}

        event_type = header.get("event_type", "")
        event = obj.get("event") or {}
        msg = event.get("message") or {}
        sender = event.get("sender") or {}
        sender_oid = sender.get("sender_id", {}).get("open_id", "")
        msg_type = msg.get("message_type", "")
        content_raw = msg.get("content", "")

        content_text = ""
        file_key = ""
        try:
            parsed = json.loads(content_raw)
            if msg_type == "text":
                content_text = parsed.get("text", "")
            elif msg_type == "post":
                # v3.1: extract plain text from Lark post (rich text) by walking
                # the content tree. Title (if any) → first line.
                content_text = _extract_post_text(parsed)
            elif msg_type in ("file", "image", "audio"):
                file_key = parsed.get("image_key", parsed.get("file_key", ""))
        except json.JSONDecodeError:
            content_text = content_raw

        storage.record_event(
            event_id=event_id, sender_oid=sender_oid, event_type=event_type,
            msg_type=msg_type, size_bytes=len(raw_body), content_hash=text_hash(content_raw),
            summary=(content_text or "")[:30],
        )

        if event_type != "im.message.receive_v1":
            storage.mark_event_handled(event_id)
            return {"status": "ignored"}

        incoming = IncomingMessage(
            event_id=event_id, sender_open_id=sender_oid,
            chat_type=msg.get("chat_type", "p2p"), msg_type=msg_type,
            content_raw=content_raw, content_text=content_text,
            chat_id=msg.get("chat_id", ""),
            create_time=msg.get("create_time", ""),
            message_id=msg.get("message_id", ""),
            file_key=file_key,
        )
        await queue.enqueue(
            "incoming_message",
            incoming.__dict__,
            requester_oid=sender_oid,
        )
        storage.mark_event_handled(event_id)
        return {"status": "ok"}

    return api
