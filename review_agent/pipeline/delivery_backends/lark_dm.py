from __future__ import annotations

from pathlib import Path

from ...core.models import Session
from ...lark.client import LarkClient
from ...util.md import text_hash
from .base import DeliveryBackend, DeliveryResult, DeliveryTarget


class LarkDmBackend(DeliveryBackend):
    name = "lark_dm"

    def __init__(self, client: LarkClient, max_chars: int = 4000):
        self.client = client
        self.max_chars = max_chars

    async def deliver(
        self,
        target: DeliveryTarget,
        session: Session,
        ctx: dict,
    ) -> DeliveryResult:
        fs = Path(session.fs_path)
        parts: list[str] = []
        if "summary" in target.payload:
            summary = (fs / "summary.md").read_text(encoding="utf-8")
            parts.append(summary)
        doc_url = ctx.get("doc_url", "")
        if doc_url:
            parts.append(f"\n📄 Lark Doc: {doc_url}")
        text = "\n\n".join(parts).strip()
        if len(text) > self.max_chars:
            text = text[: self.max_chars - 20] + "\n…(truncated)"
        msg_id = await self.client.send_dm_text(target.open_id, text)
        return DeliveryResult(
            backend=self.name, ok=True, lark_msg_id=msg_id,
            detail=f"sent {len(text)} chars to {target.open_id}",
        )

    @staticmethod
    def content_hash_for(target: DeliveryTarget, session: Session, ctx: dict) -> str:
        fs = Path(session.fs_path)
        body = ""
        if "summary" in target.payload and (fs / "summary.md").exists():
            body = (fs / "summary.md").read_text(encoding="utf-8")
        return text_hash(body + ctx.get("doc_url", ""))
