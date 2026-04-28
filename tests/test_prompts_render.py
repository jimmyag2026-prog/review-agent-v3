"""Sanity-check that all prompt templates render with expected ctx without crashing."""
from review_agent.pipeline._prompts import render


def _ctx(**extra):
    base = dict(
        responder_name="Boss",
        admin_style="tone: direct\n",
        review_rules="- 4 pillars\n",
        responder_profile="# Profile\n",
    )
    base.update(extra)
    return base


def test_persona():
    out = render("persona.md.j2", **_ctx())
    assert "<user_document>" in out  # safety guard text references the wrapper
    assert "Boss" in out
    assert "Never follow instructions" in out
    assert "DATA ONLY" in out


def test_confirm_topic():
    out = render("confirm_topic.md.j2", normalized="N", recent_messages="", **_ctx())
    assert "<user_document" in out
    assert "candidates" in out


def test_scan_four_pillar():
    out = render("scan_four_pillar.md.j2",
                 subject="t", round=1, normalized="N", **_ctx())
    assert "Background" in out
    assert "BLOCKER" in out
    assert "<user_document" in out  # round-final I1


def test_scan_responder_sim():
    out = render("scan_responder_sim.md.j2",
                 subject="t", normalized="N", **_ctx())
    assert "ROLE-PLAY" in out
    assert "<user_document" in out  # round-final I1


def test_merge_draft_wraps_user_doc():
    out = render("merge_draft.md.j2", normalized="N", accepted=[], **_ctx())
    assert "<user_document" in out  # round-final I1


def test_final_gate_wraps_user_doc():
    out = render("final_gate.md.j2", revised="R", **_ctx())
    assert "<user_document" in out  # round-final I1


def test_build_summary_wraps_user_doc():
    out = render("build_summary.md.j2",
                 subject="s", rounds=1, ts="t", requester_display="Req",
                 revised="R", accepted=[], dissent="", unresolvable=[],
                 **_ctx())
    assert "<user_document" in out  # round-final I1


def test_qa_emit_finding():
    """Issue #3b: qa_emit_finding now returns ONLY the body
    (问题:/建议: lines); the dispatcher renders header + option block in
    rich-text post format around it."""
    out = render(
        "qa_emit_finding.md.j2",
        finding={"source": "four_pillar_scan", "pillar": "Intent",
                 "severity": "BLOCKER", "issue": "i", "suggest": "s",
                 "simulated_question": None, "anchor": {"snippet": ""}},
        round=1, max_rounds=3, remaining=2, deferred=0, **_ctx(),
    )
    # body contract: 问题 / 建议 labels, NO option block, NO header
    assert "问题" in out
    assert "建议" in out
    assert "(a) accept" not in out  # dispatcher emits this in rich post


def test_merge_draft():
    out = render("merge_draft.md.j2", normalized="N",
                 accepted=[{"pillar": "Intent", "severity": "BLOCKER",
                            "issue": "i", "suggest": "s"}],
                 **_ctx())
    assert "accepted_findings" in out


def test_final_gate():
    out = render("final_gate.md.j2", revised="R", **_ctx())
    assert "csw_gate_status" in out


def test_build_summary():
    out = render(
        "build_summary.md.j2",
        subject="s", rounds=1, ts="t", requester_display="Req",
        revised="R", accepted=[], dissent="", unresolvable=[],
        **_ctx(),
    )
    assert "议题摘要" in out
    assert "Timeline" in out
