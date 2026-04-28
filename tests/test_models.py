from review_agent.core.enums import (
    FindingSource,
    FindingStatus,
    Pillar,
    Severity,
)
from review_agent.core.models import Anchor, Cursor, Finding


def test_cursor_advance():
    c = Cursor(current_id="p1", pending=["p2", "p3"])
    nxt = c.advance()
    assert nxt == "p2"
    assert c.current_id == "p2"
    assert "p1" in c.done


def test_cursor_pull_deferred():
    c = Cursor(deferred=["p7", "p8", "p9"])
    moved = c.pull_deferred(2)
    assert moved == 2
    assert c.pending == ["p7", "p8"]
    assert c.deferred == ["p9"]


def test_finding_to_jsonl_minimal():
    f = Finding(
        id="p1", round=1, created_at="2026-04-27T00:00:00Z",
        source=FindingSource.FOUR_PILLAR, pillar=Pillar.INTENT,
        severity=Severity.BLOCKER, issue="ask 不清", suggest="改成 X",
        anchor=Anchor(snippet="原文片段"),
    )
    d = f.to_jsonl()
    assert d["pillar"] == "Intent"
    assert d["severity"] == "BLOCKER"
    assert d["status"] == "open"
    assert d["anchor"]["snippet"] == "原文片段"


def test_cursor_serde():
    c = Cursor(current_id="p1", pending=["p2"], regression_rescan=True)
    d = c.to_dict()
    c2 = Cursor.from_dict(d)
    assert c2.current_id == "p1"
    assert c2.regression_rescan is True
