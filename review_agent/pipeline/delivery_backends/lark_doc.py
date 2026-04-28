from __future__ import annotations

from pathlib import Path

from ...core.models import Session
from ...lark.client import LarkClient
from .base import DeliveryBackend, DeliveryResult, DeliveryTarget


class LarkDocBackend(DeliveryBackend):
    """Round-2 NI2: v0 必需 — close 时把 summary + final 上传成一份 Lark Doc。"""

    name = "lark_doc"

    def __init__(self, client: LarkClient):
        self.client = client

    async def deliver(
        self,
        target: DeliveryTarget,
        session: Session,
        ctx: dict,
    ) -> DeliveryResult:
        fs = Path(session.fs_path)
        title = f"Review · {session.subject or session.id}"
        parts: list[str] = []
        if "summary" in target.payload and (fs / "summary.md").exists():
            parts.append((fs / "summary.md").read_text(encoding="utf-8"))
        if "final" in target.payload:
            final = fs / "final" / "revised.md"
            if final.exists():
                parts.append("\n\n---\n\n# 最终材料\n\n" + final.read_text(encoding="utf-8"))
        body = "\n".join(parts)
        doc = await self.client.create_doc(title, body)
        doc_id = doc.get("document_id", "")
        url = ""
        if doc_id:
            url = f"https://feishu.cn/docx/{doc_id}"
        return DeliveryResult(backend=self.name, ok=bool(doc_id), doc_url=url, detail=doc_id)
