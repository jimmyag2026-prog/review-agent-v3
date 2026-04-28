import pytest

from review_agent.llm.base import LLMOutputParseError, LLMTerminalFailure
from review_agent.pipeline._json import extract


def test_pure_json():
    assert extract('{"a": 1}') == {"a": 1}


def test_fenced():
    assert extract("```json\n{\"a\":1}\n```") == {"a": 1}


def test_with_prose():
    s = "Here is your output:\n```json\n{\"x\":2,\"y\":3}\n```\nThanks"
    assert extract(s) == {"x": 2, "y": 3}


def test_trailing_comma():
    assert extract("{\n  \"a\": 1,\n}") == {"a": 1}


def test_array():
    assert extract("[1,2,3]") == [1, 2, 3]


def test_no_json():
    """Issue #6: parse failure raises LLMOutputParseError (a subclass of
    LLMTerminalFailure) so dispatcher's existing terminal-failure handler
    catches it and runs the session through _fail_session cleanly."""
    with pytest.raises(LLMOutputParseError):
        extract("nothing here")
    with pytest.raises(LLMTerminalFailure):  # also catchable by parent
        extract("nothing here")


def test_empty_content():
    """Empty / whitespace content also raises LLMOutputParseError (covers the
    deepseek-v4-flash empty-response bug from 2026-04-28 live test)."""
    with pytest.raises(LLMOutputParseError, match="empty"):
        extract("")
    with pytest.raises(LLMOutputParseError):
        extract("   \n  ")
    with pytest.raises(LLMOutputParseError):
        extract(None)  # type: ignore[arg-type]
