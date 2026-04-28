"""Lenient JSON extraction from LLM output.

LLMs often wrap JSON in markdown fences, prepend prose, or trail commas.
This helper salvages the first valid JSON object/array from the raw content.

Issue #6: parse failures raise LLMOutputParseError (a subclass of
LLMTerminalFailure) so dispatcher's existing terminal-failure handler
catches it and runs the session through _fail_session — instead of letting
ValueError bubble to the worker and crash the task silently.
"""
from __future__ import annotations

import json
import re

from ..llm.base import LLMOutputParseError


def extract(text: str) -> dict | list:
    s = (text or "").strip()
    if not s:
        raise LLMOutputParseError("LLM returned empty content (no JSON to parse)")
    # strip markdown fence
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```\s*$", "", s)
    # quick path
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # find first { ... } or [ ... ] balanced
    for opener, closer in [("{", "}"), ("[", "]")]:
        start = s.find(opener)
        if start < 0:
            continue
        depth = 0
        for i in range(start, len(s)):
            if s[i] == opener:
                depth += 1
            elif s[i] == closer:
                depth -= 1
                if depth == 0:
                    chunk = s[start : i + 1]
                    chunk = re.sub(r",(\s*[}\]])", r"\1", chunk)  # trailing commas
                    try:
                        return json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
    raise LLMOutputParseError(f"no JSON found in LLM content: {s[:200]!r}")
