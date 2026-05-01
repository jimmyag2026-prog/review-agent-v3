from review_agent.core.enums import FindingSource, FindingStatus, Pillar, Severity, Stage
from review_agent.core.models import Anchor, Cursor, Finding
from review_agent.pipeline import qa_loop


def _seed_findings(storage, s, ids_pillars):
    for fid, pillar, sev in ids_pillars:
        f = Finding(
            id=fid, round=1, created_at="t",
            source=FindingSource.FOUR_PILLAR, pillar=pillar, severity=sev,
            issue=f"issue {fid}", suggest=f"do {fid}",
            anchor=Anchor(snippet=""),
        )
        storage.append_finding(s, f)


def test_accept_advances(session_setup):
    storage, s, *_ = session_setup
    _seed_findings(storage, s, [
        ("p1", Pillar.INTENT, Severity.BLOCKER),
        ("p2", Pillar.MATERIALS, Severity.IMPROVEMENT),
    ])
    storage.save_cursor(s, Cursor(current_id="p1", pending=["p2"]))
    o = qa_loop.handle_reply(storage=storage, session=s, reply="a", top_n_more=5)
    assert o.action == "emit_next"
    assert o.advanced
    cur = storage.load_cursor(s)
    assert cur.current_id == "p2"
    findings = storage.load_findings(s)
    assert next(f for f in findings if f["id"] == "p1")["status"] == FindingStatus.ACCEPTED.value


def test_reject_writes_dissent(session_setup):
    storage, s, *_ = session_setup
    _seed_findings(storage, s, [("p1", Pillar.MATERIALS, Severity.BLOCKER)])
    storage.save_cursor(s, Cursor(current_id="p1", pending=[]))
    qa_loop.handle_reply(storage=storage, session=s,
                         reply="b 这数据公司没法公开", top_n_more=5)
    from pathlib import Path
    dissent = (Path(s.fs_path) / "dissent.md").read_text()
    assert "p1" in dissent
    assert "数据公司" in dissent


def test_modify(session_setup):
    storage, s, *_ = session_setup
    _seed_findings(storage, s, [("p1", Pillar.FRAMEWORK, Severity.IMPROVEMENT)])
    storage.save_cursor(s, Cursor(current_id="p1", pending=[]))
    qa_loop.handle_reply(storage=storage, session=s,
                         reply="c 我要改成 按 cost 比较", top_n_more=5)
    f = storage.load_findings(s)[0]
    assert f["status"] == FindingStatus.MODIFIED.value
    assert "按 cost" in f["reply"]


def test_pass_no_status_change(session_setup):
    storage, s, *_ = session_setup
    _seed_findings(storage, s, [("p1", Pillar.MATERIALS, Severity.NICE_TO_HAVE)])
    storage.save_cursor(s, Cursor(current_id="p1", pending=[]))
    qa_loop.handle_reply(storage=storage, session=s, reply="pass", top_n_more=5)
    assert storage.load_findings(s)[0]["status"] == FindingStatus.OPEN.value


def test_more_pulls_deferred(session_setup):
    storage, s, *_ = session_setup
    _seed_findings(storage, s, [
        ("p1", Pillar.MATERIALS, Severity.NICE_TO_HAVE),
        ("p2", Pillar.MATERIALS, Severity.NICE_TO_HAVE),
    ])
    storage.save_cursor(s, Cursor(deferred=["p1", "p2"]))
    o = qa_loop.handle_reply(storage=storage, session=s, reply="more", top_n_more=5)
    cur = storage.load_cursor(s)
    assert cur.pending == ["p2"] or cur.current_id == "p1"
    assert o.action == "emit_next"


def test_done_proposes_close(session_setup):
    storage, s, *_ = session_setup
    storage.save_cursor(s, Cursor(current_id=None, pending=[]))
    o = qa_loop.handle_reply(storage=storage, session=s, reply="done", top_n_more=5)
    assert o.action == "propose_close"


def test_force_close(session_setup):
    storage, s, *_ = session_setup
    o = qa_loop.handle_reply(storage=storage, session=s, reply="force-close 没时间了", top_n_more=5)
    assert o.action == "force_close"


def test_cursor_exhausted_with_deferred_proposes_close(session_setup):
    """Regression: after regression_rescan finishes its last item, cursor ends
    up current_id=None / pending=[] but session.stage stays qa_active. Any
    subsequent reply previously returned no_op and the dispatcher silently
    swallowed it. Now it must propose_close so the user gets a DM and a way
    out (a / more / done)."""
    storage, s, *_ = session_setup
    storage.save_cursor(s, Cursor(
        current_id=None, pending=[],
        deferred=["d1", "d2", "d3"],
    ))
    o = qa_loop.handle_reply(storage=storage, session=s, reply="hello", top_n_more=5)
    assert o.action == "propose_close"
    assert "deferred" in o.dm_text
    assert "3" in o.dm_text


def test_cursor_exhausted_no_deferred_proposes_close(session_setup):
    """Same exhausted-cursor case but with no deferred items either: still
    propose_close (without the deferred mention) so the user can reply `a`
    to close out."""
    storage, s, *_ = session_setup
    storage.save_cursor(s, Cursor(current_id=None, pending=[], deferred=[]))
    o = qa_loop.handle_reply(storage=storage, session=s, reply="结果出来了嘛", top_n_more=5)
    assert o.action == "propose_close"
    assert "deferred" not in o.dm_text


def test_regression_reopen(session_setup):
    storage, s, *_ = session_setup
    _seed_findings(storage, s, [("p1", Pillar.INTENT, Severity.BLOCKER)])
    storage.save_cursor(s, Cursor())
    cur = qa_loop.transition_after_final_gate_fail(
        storage=storage, session=s, regression_finding_ids=["p1"],
    )
    assert cur.regression_rescan is True
    assert cur.current_id == "p1" or "p1" in cur.pending
    refreshed = storage.get_session(s.id)
    assert refreshed.stage == Stage.QA_ACTIVE_REOPENED
