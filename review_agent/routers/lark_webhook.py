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

        # url_verification (initial setup)
        if obj.get("type") == "url_verification":
            return {"challenge": obj.get("challenge", "")}

        # signature verify (round-1 B4)
        if encrypt_key and not wh.verify_v2_signature(request.headers, raw_body, encrypt_key):
            raise HTTPException(401, "bad signature")

        if "encrypt" in obj:
            obj = wh.decrypt_aes(obj["encrypt"], encrypt_key)

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
        try:
            if msg_type == "text":
                content_text = json.loads(content_raw).get("text", "")
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
        )
        await queue.enqueue(
            "incoming_message",
            incoming.__dict__,
            requester_oid=sender_oid,
        )
        storage.mark_event_handled(event_id)
        return {"status": "ok"}

    return api
