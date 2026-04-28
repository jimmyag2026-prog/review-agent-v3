from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IncomingMessage:
    event_id: str
    sender_open_id: str
    chat_type: str  # "p2p" | "group"
    msg_type: str  # "text" | "file" | "image" | "audio" | "post"
    content_raw: str  # JSON-encoded by Lark
    content_text: str  # extracted plain text where applicable
    chat_id: str
    create_time: str  # ms timestamp string
    message_id: str
