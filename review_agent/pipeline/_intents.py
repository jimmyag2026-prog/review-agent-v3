"""Shared reply-intent parser used by subject_confirmation + qa_loop."""
from __future__ import annotations

import re

from ..core.enums import Intent

_PASS = {"p", "pass", "skip", "next", "跳过", "下一条", "下一个"}
_MORE = {"more", "继续", "下一批", "deferred"}
_DONE = {"done", "close", "结束", "可以了", "ready"}
_FORCE_CLOSE = {"forceclose", "强制关", "必须 close", "force-close", "force close"}
_ACCEPT_PREFIX = ("a", "accept", "好", "ok", "同意")
_REJECT_PREFIX = ("b", "reject", "不同意", "不行", "no")
_MODIFY_PREFIX = ("c", "modify", "改成", "我改成")
_PICK_A = ("a",)
_PICK_B = ("b",)
_PICK_C = ("c",)


def parse_reply_intent(text: str, *, stage: str) -> tuple[Intent, str]:
    """Return (intent, remainder). For subject_confirmation, a/b/c map to PICK_A/B/C.
    For qa_loop, a/b/c map to ACCEPT/REJECT/MODIFY."""
    raw = (text or "").strip()
    low = raw.lower()
    first_token = re.split(r"[\s,，:：。.]+", low, maxsplit=1)[0]

    if low in _PASS or first_token in _PASS:
        return Intent.PASS, _strip_prefix(raw, first_token)
    if low in _MORE or first_token in _MORE:
        return Intent.MORE, ""
    if low in _DONE or first_token in _DONE:
        return Intent.DONE, ""
    if low in _FORCE_CLOSE or any(low.startswith(s) for s in _FORCE_CLOSE):
        return Intent.FORCE_CLOSE, raw

    if stage == "subject_confirmation":
        if first_token in _PICK_A:
            return Intent.PICK_A, ""
        if first_token in _PICK_B:
            return Intent.PICK_B, ""
        if first_token in _PICK_C:
            return Intent.PICK_C, ""
        if low.startswith("custom") or low.startswith("其他"):
            return Intent.CUSTOM, _strip_prefix(raw, first_token)
        if len(raw) > 20:
            return Intent.CUSTOM, raw
        return Intent.CUSTOM, raw

    # qa_loop / default
    if first_token in _PICK_A or low.startswith(_ACCEPT_PREFIX):
        return Intent.ACCEPT, _strip_prefix(raw, first_token)
    if first_token in _PICK_B or low.startswith(_REJECT_PREFIX):
        return Intent.REJECT, _strip_prefix(raw, first_token)
    if first_token in _PICK_C or low.startswith(_MODIFY_PREFIX):
        return Intent.MODIFY, _strip_prefix(raw, first_token)
    if low.startswith("?") or low.endswith("?") or low.startswith("为什么"):
        return Intent.QUESTION, raw
    if low.startswith("custom") or len(raw) > 20:
        return Intent.CUSTOM, raw
    return Intent.CUSTOM, raw


def _strip_prefix(raw: str, prefix: str) -> str:
    s = raw.strip()
    low = s.lower()
    if low.startswith(prefix):
        s = s[len(prefix):]
    return s.lstrip(" :,.，：。").strip()
