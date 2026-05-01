"""
Slack DM delivery backend — sends review-agent output to a Slack user.

Uses SlackAdapter's send_dm() method, which handles:
  - conversations_open for DM channel resolution
  - markdown → Slack mrkdwn conversion
  - message length truncation to 39,000 chars

Plugs into the existing delivery pipeline (delivery_backends pattern):
  DeliveryBackend.deliver(target, session, ctx) → DeliveryResult
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ...core.models import Session
from ...util.md import text_hash
from .base import DeliveryBackend, DeliveryResult, DeliveryTarget


class SlackDmBackend(DeliveryBackend):
    """Deliver review-agent summary/output to a Slack user via DM."""

    name = "slack_dm"

    def __init__(self, adapter, max_chars: int = 39000):
        """Initialize with a SlackAdapter instance.

        Args:
            adapter: SlackAdapter (must be started)
            max_chars: Slack message limit (buffer-safe at 39,000)
        """
        if adapter is None:
            raise ValueError("SlackDmBackend requires a SlackAdapter")
        self.adapter = adapter
        self.max_chars = max_chars

    async def deliver(
        self,
        target: DeliveryTarget,
        session: Session,
        ctx: dict,
    ) -> DeliveryResult:
        """Send summary content to the Slack user.

        Reads summary.md from the session's filesystem path, appends
        doc_url if available from the delivery context, and sends
        the combined text as a Slack DM.
        """
        fs = Path(session.fs_path)
        parts: list[str] = []

        # Read summary if requested
        if "summary" in target.payload:
            try:
                summary_path = fs / "summary.md"
                if summary_path.exists():
                    summary = summary_path.read_text(encoding="utf-8")
                    parts.append(summary)
            except Exception as e:
                return DeliveryResult(
                    backend=self.name,
                    ok=False,
                    detail=f"failed to read summary: {e}",
                )

        # Append doc URL if available (from Lark doc or other backends)
        doc_url = ctx.get("doc_url", "")
        if doc_url:
            parts.append(f"\n📄 Full report: {doc_url}")

        text = "\n\n".join(parts).strip()
        if not text:
            return DeliveryResult(
                backend=self.name,
                ok=False,
                detail="nothing to deliver (empty summary)",
            )

        # Send via Slack adapter
        try:
            msg_id = await self.adapter.send_dm(target.open_id, text)
            return DeliveryResult(
                backend=self.name,
                ok=True,
                lark_msg_id=msg_id,  # overloaded: Slack message ts
                detail=f"slack DM sent ({len(text)} chars) to {target.open_id}",
            )
        except Exception as e:
            return DeliveryResult(
                backend=self.name,
                ok=False,
                detail=f"slack DM failed: {e}",
            )

    @staticmethod
    def content_hash_for(
        target: DeliveryTarget,
        session: Session,
        ctx: dict,
    ) -> str:
        """Generate a content hash for dedup — prevents duplicate delivery."""
        fs = Path(session.fs_path)
        body = ""
        summary_path = fs / "summary.md"
        if "summary" in target.payload and summary_path.exists():
            body = summary_path.read_text(encoding="utf-8")
        return text_hash(body + ctx.get("doc_url", ""))
