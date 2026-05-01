"""
Slack-specific type helpers.

Maps Slack event payloads to review_agent's generic IncomingMessage,
and handles user identity resolution for Slack user IDs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SlackEventContext:
    """Parsed context from a Slack message event."""
    user_id: str         # Slack user ID (e.g., "U12345")
    user_name: str = ""  # Resolved display name
    channel_id: str = "" # Channel or DM ID
    team_id: str = ""    # Workspace team ID
    thread_ts: str = ""  # Thread parent timestamp (for thread replies)
    msg_ts: str = ""     # This message's timestamp
    text: str = ""       # Cleaned message text
    is_dm: bool = False  # True for IM/DM channels
    is_mentioned: bool = False  # True if bot was @mentioned
    msg_type: str = "text"  # "text" | "file" | "image" | "audio"
    file_url: str = ""   # Private file download URL
    file_name: str = ""  # Original filename
    file_mimetype: str = ""  # MIME type
    event_id: str = ""   # Unique event ID for dedup


def extract_context(event: dict, bot_user_id: str) -> SlackEventContext:
    """Parse a Slack message event dict into a SlackEventContext.

    Handles message subtypes (regular, bot_message, file_share, thread_reply).
    """
    user_id = event.get("user", "")
    channel_id = event.get("channel", "")
    team_id = event.get("team", event.get("team_id", ""))
    thread_ts_raw = event.get("thread_ts", "")
    msg_ts = event.get("ts", "")
    text_raw = event.get("text", "")
    subtype = event.get("subtype", "")

    # Determine channel type from ID prefix
    channel_type = event.get("channel_type", "")
    if not channel_type:
        if channel_id.startswith("D"):
            channel_type = "im"
        elif channel_id.startswith("C"):
            channel_type = "channel"
    is_dm = channel_type == "im"

    # Check if bot is @mentioned
    is_mentioned = False
    if bot_user_id and f"<@{bot_user_id}>" in text_raw:
        is_mentioned = True
        text_raw = text_raw.replace(f"<@{bot_user_id}>", "").strip()

    # Determine thread_ts: in DMs, only real threads get thread_ts;
    # in channels, use msg_ts as fallback (bot always replies in thread)
    if is_dm:
        thread_ts = thread_ts_raw or ""
    else:
        thread_ts = thread_ts_raw or msg_ts

    # Extract file info if present
    msg_type = "text"
    file_url = ""
    file_name = ""
    file_mimetype = ""
    files = event.get("files", [])
    if files:
        f = files[0]
        file_mimetype = f.get("mimetype", "")
        file_url = f.get("url_private_download", f.get("url_private", ""))
        file_name = f.get("name", "")
        if file_mimetype.startswith("image/"):
            msg_type = "image"
        elif file_mimetype.startswith("audio/"):
            msg_type = "audio"
        else:
            msg_type = "file"

    # Clean text: strip Slack's special formatting markers
    text = _clean_slack_text(text_raw)

    return SlackEventContext(
        user_id=user_id,
        channel_id=channel_id,
        team_id=team_id,
        thread_ts=thread_ts,
        msg_ts=msg_ts,
        text=text,
        is_dm=is_dm,
        is_mentioned=is_mentioned,
        msg_type=msg_type,
        file_url=file_url,
        file_name=file_name,
        file_mimetype=file_mimetype,
        event_id=f"{channel_id}:{msg_ts}",
    )


def _clean_slack_text(text: str) -> str:
    """Strip Slack formatting markers from message text.
    
    - <@U12345> → @mention (but bot mentions already stripped)
    - <#C12345|channel-name> → #channel-name
    - <!channel> → @channel
    - <!everyone> → @everyone
    """
    # Channel references: <#C12345|name> → #name
    text = re.sub(r"<#C\w+\|([^>]+)>", r"#\1", text)
    # User mentions (remaining): <@U12345> → @user
    text = re.sub(r"<@(\w+)>", r"@\1", text)
    # Special mentions
    text = text.replace("<!channel>", "@channel")
    text = text.replace("<!everyone>", "@everyone")
    text = text.replace("<!here>", "@here")
    # Strip <mailto:...> links
    text = re.sub(r"<mailto:([^|>]+)\|[^>]+>", r"\1", text)
    text = re.sub(r"<mailto:([^>]+)>", r"\1", text)
    # Convert generic <url|label> → label (url)
    text = re.sub(r"<([^|>]+)\|([^>]+)>", r"\2", text)
    # Convert bare <url> → url
    text = re.sub(r"<([^>]+)>", r"\1", text)
    return text
