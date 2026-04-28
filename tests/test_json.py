import pytest

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
    with pytest.raises(ValueError):
        extract("nothing here")
