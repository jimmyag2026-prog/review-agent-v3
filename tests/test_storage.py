from review_agent.core.enums import (
    FindingSource,
    FindingStatus,
    Pillar,
    Role,
    SessionStatus,
    Severity,
    Stage,
)
from review_agent.core.models import Anchor, Cursor, Finding, User


def test_user_roundtrip(tmp_storage):
    u = User(open_id="ou_x", display_name="X", roles=[Role.ADMIN])
    tmp_storage.upsert_user(u)
    got = tmp_storage.get_user("ou_x")
    assert got is not None
    assert got.has_role(Role.ADMIN)


def test_create_session_creates_files(session_setup):
    storage, s, *_ = session_setup
    from pathlib import Path
    fs = Path(s.fs_path)
    assert (fs / "admin_style.md").exists()
    assert (fs / "review_rules.md").exists()
    assert (fs / "profile.md").exists()
    assert (fs / "annotations.jsonl").exists()
    assert (fs / "cursor.json").exists()


def test_active_session_lookup(session_setup):
    storage, s, _admin, requester = session_setup
    found = storage.get_active_session_for(requester.open_id)
    assert found is not None
    assert found.id == s.id


def test_append_and_load_finding(session_setup):
    storage, s, *_ = session_setup
    f = Finding(
        id="p1", round=1, created_at="t",
        source=FindingSource.FOUR_PILLAR, pillar=Pillar.INTENT,
        severity=Severity.BLOCKER, issue="i", suggest="s",
        anchor=Anchor(snippet="snip"),
    )
    storage.append_finding(s, f)
    got = storage.load_findings(s)
    assert len(got) == 1
    assert got[0]["id"] == "p1"


def test_update_finding_status(session_setup):
    storage, s, *_ = session_setup
    f = Finding(id="p1", round=1, created_at="t", source=FindingSource.FOUR_PILLAR,
                pillar=Pillar.MATERIALS, severity=Severity.IMPROVEMENT,
                issue="i", suggest="s")
    storage.append_finding(s, f)
    storage.update_finding_status(s, "p1", status="accepted", reply="好的")
    got = storage.load_findings(s)[0]
    assert got["status"] == "accepted"
    assert got["reply"] == "好的"


def test_dissent_append(session_setup):
    storage, s, *_ = session_setup
    storage.append_dissent(s, {"id": "p1", "pillar": "Materials", "severity": "BLOCKER",
                               "issue": "缺数据", "suggest": "补"}, "数据保密无法补")
    from pathlib import Path
    body = (Path(s.fs_path) / "dissent.md").read_text()
    assert "缺数据" in body
    assert "数据保密" in body


def test_event_dedup(tmp_storage):
    assert not tmp_storage.event_seen("e1")
    tmp_storage.record_event(
        "e1", sender_oid="ou_a", event_type="im", msg_type="text",
        size_bytes=10, content_hash="h", summary="hi",
    )
    assert tmp_storage.event_seen("e1")


def test_task_recovery(tmp_storage):
    tid = tmp_storage.insert_task("scan", {"x": 1}, requester_oid="ou_r")
    tmp_storage.mark_task_running(tid)
    n = tmp_storage.recover_running_tasks()
    assert n == 1
    pending = list(tmp_storage.list_pending_tasks())
    assert len(pending) == 1


def test_outbound_dedup(session_setup):
    storage, s, *_ = session_setup
    storage.log_outbound(
        session_id=s.id, to_open_id="ou_responder", msg_type="lark_dm",
        content_hash="hash1", lark_msg_id="m1", ok=True, error=None,
    )
    assert storage.outbound_already_sent(s.id, "ou_responder", "hash1")
    assert not storage.outbound_already_sent(s.id, "ou_responder", "hash2")
