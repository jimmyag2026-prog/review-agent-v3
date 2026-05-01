"""
Slack adapter — Socket Mode event ingestion for review-agent.

Architecture (Path A — standalone, no Hermes gateway):
  Slack Socket Mode WS
    → slack_bolt.AsyncApp + AsyncSocketModeHandler
    → _handle_message() [dedup, mention-gate, bot-filter, thread-detect]
    → SlackEventContext → IncomingMessage mapping
    → queue.enqueue("incoming_message", ...)
    → Dispatcher.dispatch() (existing pipeline, unchanged)
    → SlackClient.send_dm_text() (delivery backend)

Design mirrors Hermes SlackAdapter patterns:
  - MessageDeduplicator: per-event-id TTL cache to prevent Socket Mode redeliveries
  - Mention gating: in public channels, require @bot mention (DMs bypass)
  - Thread detection: channel messages = thread replies; DMs = optional threads
  - Bot-self filtering: ignore events from our own bot_user_id

Socket Mode connection runs as an asyncio background task within the FastAPI
lifespan — no separate process needed. Multi-workspace support via _team_clients
dict keyed by team_id.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from ..core.storage import Storage
from ..lark.types import IncomingMessage
from ..tasks.queue import TaskQueue
from ..util import log

_logger = log.get(__name__)

try:
    from slack_bolt.app.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_sdk.web.async_client import AsyncWebClient as SlackAsyncWebClient
    SLACK_AVAILABLE = True
except ImportError:
    SLACK_AVAILABLE = False
    AsyncApp = None  # type: ignore
    AsyncSocketModeHandler = None  # type: ignore
    SlackAsyncWebClient = None  # type: ignore
    _logger.warning("slack-bolt/slack-sdk not installed; Slack adapter disabled")


# ── Slack mrkdwn imports ─────────────────────────────
from .mrkdwn import markdown_to_slack, truncate_for_slack, MAX_MESSAGE_LENGTH


# ── Deduplication cache ──────────────────────────────
# Socket Mode may redeliver events.  Track seen (channel_id, msg_ts) pairs
# with a TTL to avoid processing duplicates.
class _MessageDedup:
    def __init__(self, max_size: int = 2000, ttl_seconds: float = 300.0):
        self._max = max_size
        self._ttl = ttl_seconds
        self._cache: dict[str, float] = {}  # key → expiry monotonic timestamp

    def seen(self, key: str) -> bool:
        now = time.monotonic()
        if key in self._cache and self._cache[key] > now:
            return True
        self._cache[key] = now + self._ttl
        if len(self._cache) > self._max:
            # evict oldest 10%
            oldest = sorted(self._cache.items(), key=lambda x: x[1])[: self._max // 10]
            for k, _ in oldest:
                self._cache.pop(k, None)
        return False


# ── IncomingMessage factory ─────────────────────────

def slack_to_incoming(ctx: dict) -> IncomingMessage:
    """Build a platform-agnostic IncomingMessage from Slack event context.

    The `sender_open_id` field is overloaded to carry the Slack user ID.
    The `chat_id` field carries the Slack channel ID.
    The `file_key` field carries the Slack file download URL.
    """
    return IncomingMessage(
        event_id=ctx.get("event_id", ""),
        sender_open_id=ctx.get("user_id", ""),
        chat_type="p2p" if ctx.get("is_dm") else "group",
        msg_type=ctx.get("msg_type", "text"),
        content_raw=json.dumps(ctx, ensure_ascii=False),
        content_text=ctx.get("text", ""),
        chat_id=ctx.get("channel_id", ""),
        create_time=str(int(time.time() * 1000)),
        message_id=ctx.get("event_id", ""),
        file_key=ctx.get("file_url", ""),
    )


# ── Slack adapter ───────────────────────────────────

class SlackAdapter:
    """Socket Mode Slack adapter for review-agent.

    Usage:
        adapter = SlackAdapter(
            bot_token="xoxb-...",
            app_token="xapp-...",
            storage=storage,
            queue=queue,
        )
        await adapter.start()   # starts Socket Mode WS in background task
        # ... app runs ...
        await adapter.stop()    # clean shutdown
    """

    def __init__(
        self,
        *,
        bot_token: str,
        app_token: str,
        storage: Storage,
        queue: TaskQueue,
        bot_user_id: str = "",
    ):
        if not SLACK_AVAILABLE:
            raise RuntimeError(
                "slack-bolt and slack-sdk are required. "
                "Install with: pip install slack-bolt slack-sdk"
            )
        self._bot_token = bot_token
        self._app_token = app_token
        self._storage = storage
        self._queue = queue
        self._bot_user_id = bot_user_id  # resolved at startup if empty

        # Multi-workspace: one AsyncWebClient per team_id
        self._team_clients: dict[str, SlackAsyncWebClient] = {}

        # Deduplication: prevent processing the same message twice
        self._dedup = _MessageDedup()

        # Track which threads the bot has participated in
        # (channel_id → set of thread_ts)
        self._thread_participation: dict[str, set[str]] = {}
        self._thread_participation_path: Optional[Path] = None

        # Approval button dedup: prevent double-clicks
        self._action_dedup: dict[str, bool] = {}

        # Socket Mode task handle
        self._socket_task: Optional[asyncio.Task] = None

    # ── Public start/stop ──────────────────────────

    async def start(self) -> None:
        """Start the Socket Mode handler as a background task."""
        if not self._bot_token or not self._app_token:
            _logger.info("Slack adapter: no tokens configured, skipping")
            return

        # Resolve bot_user_id if not provided
        if not self._bot_user_id:
            await self._resolve_bot_user_id()

        _logger.info(
            "Slack adapter starting (bot=%s, Socket Mode)", self._bot_user_id,
        )
        self._socket_task = asyncio.create_task(self._run_socket_mode())

    async def stop(self) -> None:
        """Gracefully shut down the Socket Mode connection."""
        if self._socket_task:
            self._socket_task.cancel()
            try:
                await self._socket_task
            except asyncio.CancelledError:
                pass
            self._socket_task = None

        # Close all team web clients
        for client in self._team_clients.values():
            # slack_sdk's AsyncWebClient doesn't have a formal close() in all versions;
            # cancel any pending requests
            pass
        self._team_clients.clear()

        # Persist thread participation state
        await self._save_thread_participation()

    # ── Socket Mode runner ─────────────────────────

    async def _run_socket_mode(self) -> None:
        """Run the Socket Mode WebSocket connection loop with reconnect logic."""
        while True:
            try:
                app = AsyncApp(token=self._bot_token)
                self._register_handlers(app)
                handler = AsyncSocketModeHandler(app, self._app_token)
                _logger.info("Slack Socket Mode connecting...")
                await handler.start_async()
            except asyncio.CancelledError:
                _logger.info("Slack Socket Mode cancelled (shutdown)")
                return
            except Exception:
                _logger.exception("Slack Socket Mode error, reconnecting in 5s...")
                await asyncio.sleep(5)

    def _register_handlers(self, app: AsyncApp) -> None:
        """Register Slack event handlers on the AsyncApp."""

        @app.event("message")
        async def handle_message(event: dict, client: SlackAsyncWebClient, say=None):
            """Handle incoming message events (DMs, channels with @mention)."""
            await self._handle_message(event, client, say)

        @app.event("app_mention")
        async def handle_mention(event: dict, client: SlackAsyncWebClient, say=None):
            """Handle explicit @bot mentions — also routes to _handle_message."""
            await self._handle_message(event, client, say)

        @app.action({"type": "block_actions"})
        async def handle_block_action(body: dict, client: SlackAsyncWebClient, ack):
            """Handle interactive block actions (e.g., approval buttons)."""
            await ack()
            # TODO: approval flow — parse action_id, update session state
            _logger.debug("block action received: %s", body.get("actions", [{}])[0].get("action_id", "?"))

    # ── Message handling ───────────────────────────

    async def _handle_message(
        self,
        event: dict,
        client: SlackAsyncWebClient,
        say=None,
    ) -> None:
        """Process a Slack message event.

        Flow:
        1) Dedup check (by msg_ts)
        2) Ignore bot messages (including our own)
        3) Mention gate for channels
        4) Extract context → map to IncomingMessage
        5) Enqueue for Dispatcher
        """
        # 1) Dedup
        dedup_key = f"{event.get('channel','')}:{event.get('ts','')}"
        if self._dedup.seen(dedup_key):
            return

        # Also dedup by message_id (some events have this)
        event_id = event.get("event_id", "")
        if event_id:
            if self._dedup.seen(event_id):
                return

        # 2) Ignore bot messages
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return
        if event.get("user") == self._bot_user_id:
            return

        # 3) Mention gate for public channels
        channel_type = event.get("channel_type", "")
        if not channel_type:
            channel_id = event.get("channel", "")
            if channel_id.startswith("D"):
                channel_type = "im"
            elif channel_id.startswith("C"):
                channel_type = "channel"

        is_dm = channel_type == "im"
        text_raw = event.get("text", "")

        if not is_dm:
            if f"<@{self._bot_user_id}>" not in text_raw:
                return  # not mentioned in channel

        # 4) Extract context
        ctx = _build_context(event, self._bot_user_id)
        if not ctx:
            return

        # 5) Build IncomingMessage and enqueue
        incoming = slack_to_incoming(ctx)

        # Store event in storage (dedup via event_id)
        event_key = ctx.get("event_id", dedup_key)
        try:
            self._storage.event_seen(event_key)
            self._storage.record_event(
                event_id=event_key,
                sender_oid=incoming.sender_open_id,
                event_type="slack_message",
                msg_type=incoming.msg_type,
                size_bytes=len(json.dumps(event, ensure_ascii=False)),
                content_hash="",  # placeholder
                summary=incoming.content_text[:30],
            )
        except Exception:
            _logger.exception("storage.record_event failed; continuing")

        # Enqueue to dispatcher
        await self._queue.enqueue(
            "incoming_message",
            incoming.__dict__,
            requester_oid=incoming.sender_open_id,
        )
        _logger.debug("slack message enqueued: %s (user=%s, dm=%s)",
                       dedup_key, ctx["user_id"], is_dm)

    # ── Direct Message sender (used by delivery backend) ──

    async def send_dm(self, user_id: str, text_markdown: str) -> str:
        """Send a DM to a Slack user.  Used by SlackDmBackend."""
        return await self._safe_dm(user_id, text_markdown)

    async def _safe_dm(self, user_id: str, text_markdown: str) -> str:
        """Send a DM with retry logic.

        Opens a DM conversation (conversations.open) if needed, then posts.
        Returns the message timestamp (msg_ts).
        """
        # Get or create web client (default team for DM)
        client = SlackAsyncWebClient(token=self._bot_token)

        # Open DM channel
        try:
            resp = await client.conversations_open(users=[user_id])
            channel_id = resp["channel"]["id"]
        except Exception:
            _logger.exception("conversations_open failed for %s", user_id)
            raise

        # Convert markdown to Slack mrkdwn
        slack_text = markdown_to_slack(text_markdown)
        slack_text = truncate_for_slack(slack_text)

        # Post message
        resp = await client.chat_postMessage(
            channel=channel_id,
            text=slack_text[:100],  # fallback notification text
            blocks=None,            # use mrkdwn in blocks
            mrkdwn=True,
        )
        # For simplicity, use the text field (Slack renders mrkdwn in text)
        # If we need richer formatting, use blocks API
        return resp.get("ts", "")

    # ── Thread reply sender ────────────────────────

    async def send_reply_to_thread(
        self,
        channel_id: str,
        thread_ts: str,
        text_markdown: str,
    ) -> str:
        """Send a reply in a Slack thread."""
        client = SlackAsyncWebClient(token=self._bot_token)
        slack_text = markdown_to_slack(text_markdown)
        slack_text = truncate_for_slack(slack_text)

        resp = await client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=slack_text,
            mrkdwn=True,
        )
        ts = resp.get("ts", "")
        # Track participation
        self._track_thread(channel_id, thread_ts)
        return ts

    # ── Bot user ID resolution ─────────────────────

    async def _resolve_bot_user_id(self) -> None:
        """Resolve the bot's own Slack user ID from auth.test API."""
        try:
            client = SlackAsyncWebClient(token=self._bot_token)
            resp = await client.auth_test()
            self._bot_user_id = resp.get("user_id", "")
            _logger.info("Slack bot user_id resolved: %s", self._bot_user_id)
        except Exception:
            _logger.warning("Could not resolve Slack bot user_id; mention gating disabled")

    # ── Thread participation tracking ──────────────

    def _track_thread(self, channel_id: str, thread_ts: str) -> None:
        """Record bot participation in a thread."""
        if channel_id not in self._thread_participation:
            self._thread_participation[channel_id] = set()
        self._thread_participation[channel_id].add(thread_ts)

    async def _save_thread_participation(self) -> None:
        """Persist thread participation to JSON file."""
        if not self._thread_participation_path or not self._thread_participation:
            return
        try:
            data = {
                channel_id: list(threads)
                for channel_id, threads in self._thread_participation.items()
            }
            import json
            self._thread_participation_path.write_text(
                json.dumps(data, indent=2), encoding="utf-8"
            )
        except Exception:
            _logger.exception("Failed to save thread participation")

    def set_persistence_path(self, path: Path) -> None:
        """Set path for persisting thread state."""
        self._thread_participation_path = path
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for ch, threads in data.items():
                    self._thread_participation[ch] = set(threads)
            except Exception:
                _logger.warning("Could not load thread participation from %s", path)


# ── Context builder ────────────────────────────────

def _build_context(event: dict, bot_user_id: str) -> dict:
    """Extract standardised context dict from a Slack message event."""
    from .types import extract_context, SlackEventContext  # noqa: F811 (circular-safe)

    ctx = extract_context(event, bot_user_id)
    return {
        "event_id": ctx.event_id or f"{ctx.channel_id}:{ctx.msg_ts}",
        "user_id": ctx.user_id,
        "user_name": ctx.user_name,
        "channel_id": ctx.channel_id,
        "team_id": ctx.team_id,
        "thread_ts": ctx.thread_ts,
        "msg_ts": ctx.msg_ts,
        "text": ctx.text,
        "is_dm": ctx.is_dm,
        "is_mentioned": ctx.is_mentioned,
        "msg_type": ctx.msg_type,
        "file_url": ctx.file_url,
        "file_name": ctx.file_name,
        "file_mimetype": ctx.file_mimetype,
    }
