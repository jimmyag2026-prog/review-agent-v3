"""Lenient JSON extraction from LLM output.

LLMs often wrap JSON in markdown fences, prepend prose, or trail commas.
This helper salvages the first valid JSON object/array from the raw content.
"""
from __future__ import annotations

import json
import re


def extract(text: str) -> dict | list:
    s = text.strip()
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
    raise ValueError(f"no JSON found in LLM content: {s[:200]!r}")
