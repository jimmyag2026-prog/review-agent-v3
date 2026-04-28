from review_agent.pipeline._format import (
    build_finding_post,
    build_text_fallback,
    _split_body,
)


def test_split_body_labeled():
    issue, suggest = _split_body("问题: ask 不清\n建议: 改成 X")
    assert issue == "ask 不清"
    assert suggest == "改成 X"


def test_split_body_chinese_colon():
    issue, suggest = _split_body("问题：缺数据\n建议：补来源")
    assert issue == "缺数据"
    assert suggest == "补来源"


def test_split_body_fallback_one_line():
    issue, suggest = _split_body("ask 不清")
    assert issue == "ask 不清"
    assert suggest == ""


def test_post_paragraphs_friendly_header_no_jargon():
    """Issue #7: requester-facing finding hides pillar/id/source/round."""
    post = build_finding_post(
        finding_id="r1", pillar="Intent", severity="BLOCKER",
        source="responder_simulation",
        body_text="问题: ask 不清\n建议: 改成 '请 X 在 Y 前批准'",
        round_no=1, max_rounds=3, remaining=4, deferred=8,
    )
    flat = " ".join(el["text"] for para in post for el in para)
    # severity → friendly Chinese label with emoji
    assert "🔴" in flat
    assert "必须修一下" in flat
    # body
    assert "ask 不清" in flat
    assert "请 X 在 Y 前批准" in flat
    # reply menu
    assert "a 改" in flat
    # NO internal jargon
    assert "BLOCKER" not in flat
    assert "Intent" not in flat
    assert "r1" not in flat
    assert "R1/3" not in flat
    assert "responder_simulation" not in flat
    assert "你模拟" not in flat


def test_severity_friendly_labels():
    for sev, em, label in [
        ("BLOCKER", "🔴", "必须修一下"),
        ("IMPROVEMENT", "🟡", "建议改一下"),
        ("NICE-TO-HAVE", "⚪", "可选"),
    ]:
        post = build_finding_post(
            finding_id="p1", pillar="Materials", severity=sev,
            source="four_pillar_scan", body_text="问题: x\n建议: y",
            round_no=1, max_rounds=3, remaining=0, deferred=0,
        )
        flat = " ".join(el["text"] for para in post for el in para)
        assert em in flat
        assert label in flat


def test_text_fallback_has_same_content():
    txt = build_text_fallback(
        finding_id="p1", pillar="Background", severity="IMPROVEMENT",
        source="four_pillar_scan", body_text="问题: 缺背景\n建议: 加 5 句",
        round_no=2, max_rounds=3, remaining=1, deferred=2,
    )
    assert "🟡" in txt
    assert "建议改一下" in txt
    assert "缺背景" in txt
    assert "加 5 句" in txt
    assert "a 改" in txt
    # no jargon leaks
    assert "Background" not in txt
    assert "IMPROVEMENT" not in txt
    assert "p1" not in txt


def test_welcome_message_is_tutorial_style():
    from review_agent.pipeline._format import welcome_message
    msg = welcome_message(requester_name="Alice", responder_name="Boss")
    assert "Alice" in msg
    assert "Boss" in msg
    # has the four numbered steps (Tester → me → Q&A → brief)
    assert "①" in msg and "②" in msg and "③" in msg and "④" in msg
    # covers v3.1 capabilities
    assert "PDF" in msg
    assert "图片" in msg
    assert "语音" in msg
    assert "飞书文档" in msg or "Lark" in msg
    # reply menu shown
    assert "a" in msg and "b" in msg and "c" in msg
    assert "pass" in msg and "more" in msg and "done" in msg
    # no internal jargon
    assert "decision-ready" not in msg
    # The acronym BLOCKER is now hidden (severity is shown as "必须修一下" in
    # findings); welcome should not surface internal classifier terms.


def test_admin_notify_message_actionable():
    from review_agent.pipeline._format import admin_notify_message
    msg = admin_notify_message(requester_name="Alice", requester_oid="ou_xxx")
    assert "Alice" in msg
    assert "ou_xxx" in msg
    assert "remove-user" in msg
