"""
Slack integration module for review-agent.

Provides:
  - SlackAdapter: Socket Mode event ingestion
  - mrkdwn conversion: markdown → Slack mrkdwn
  - types: Slack event → IncomingMessage mapping
"""
from .adapter import SlackAdapter, slack_to_incoming
from .mrkdwn import markdown_to_slack, truncate_for_slack, MAX_MESSAGE_LENGTH

__all__ = [
    "SlackAdapter",
    "slack_to_incoming",
    "markdown_to_slack",
    "truncate_for_slack",
    "MAX_MESSAGE_LENGTH",
]
