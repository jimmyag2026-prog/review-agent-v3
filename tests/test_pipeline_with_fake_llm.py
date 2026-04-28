"""End-to-end pipeline test with FakeLLMClient (no network).

Walks: scan → qa emit → final_gate. Verifies that:
- 4 + 2 findings parse and persist correctly
- cursor honours top_n
- final_gate produces verdict from canned response
"""
import asyncio
import json
from pathlib import Path

import pytest

from review_agent.core.enums import Pillar, Stage, Verdict
from review_agent.llm.fake import FakeLLMClient
from review_agent.pipeline import final_gate, scan


def _scan_layer_a():
    return json.dumps({
        "findings": [
            {"id": "p1", "pillar": "Intent", "severity": "BLOCKER",
             "issue": "ask 不清", "suggest": "改成 '请批准 X'",
             "anchor": {"line_range": [1, 1], "snippet": "我们考虑下周上线"}},
            {"id": "p2", "pillar": "Materials", "severity": "IMPROVEMENT",
             "issue": "数据无来源", "suggest": "标注来源",
             "anchor": {"line_range": [3, 3], "snippet": "用户增长不错"}},
            {"id": "p3", "pillar": "Background", "severity": "IMPROVEMENT",
             "issue": "缺背景", "suggest": "加 5 句背景",
             "anchor": {"line_range": [1, 1], "snippet": ""}},
            {"id": "p4", "pillar": "Framework", "severity": "NICE-TO-HAVE",
             "issue": "无判断维度", "suggest": "明确 cost vs speed",
             "anchor": {"line_range": [2, 2], "snippet": ""}},
        ]
    })


def _scan_layer_b():
    return json.dumps({
        "findings": [
            {"id": "r1", "pillar": "Materials", "severity": "BLOCKER",
             "priority": 1, "simulated_question": "Plan B 是什么？",
             "issue": "缺 Plan B", "suggest": "补一段 fallback",
             "anchor": {"line_range": [1, 1], "snippet": ""}},
            {"id": "r2", "pillar": "Materials", "severity": "IMPROVEMENT",
             "priority": 2, "simulated_question": "数据 source？",
             "issue": "数据 source 缺", "suggest": "补",
             "anchor": {"line_range": [1, 1], "snippet": ""}},
        ]
    })


@pytest.mark.asyncio
async def test_scan_then_final_gate(session_setup):
    storage, s, admin, requester = session_setup
    fs = Path(s.fs_path)
    (fs / "normalized.md").write_text(
        "# Plan\n我们考虑下周上线\n用户增长不错\n", encoding="utf-8"
    )
    storage.update_session(s.id, subject="是否周二发布")

    fake = FakeLLMClient()
    fake.script("deepseek-v4-pro", _scan_layer_a(), _scan_layer_b())

    cursor = await scan.run(
        storage=storage, llm=fake, model="deepseek-v4-pro",
        session=storage.get_session(s.id),
        responder_user=admin,
        admin_style="tone: direct\n", review_rules="- 4 pillars\n",
        responder_profile="# profile\n",
        top_n=5,
    )
    assert cursor.current_id is not None
    findings = storage.load_findings(storage.get_session(s.id))
    assert len(findings) == 6
    # intent BLOCKER first by prioritization
    assert cursor.current_id in ("p1", "r1")

    # round 2: final_gate scripted PASS
    fake.script("deepseek-v4-pro", json.dumps({
        "verdict": "READY", "csw_gate_status": "pass",
        "pillar_verdict": {"Background": "pass", "Materials": "pass",
                           "Framework": "pass", "Intent": "pass"},
        "regressions": [],
    }))
    (fs / "final" / "revised.md").write_text("revised body\n请批准下周二发布 v0.3", encoding="utf-8")
    outcome = await final_gate.run(
        storage=storage, llm=fake, model="deepseek-v4-pro",
        session=storage.get_session(s.id),
        responder_user=admin,
        admin_style="tone: direct\n", review_rules="- 4 pillars\n",
        responder_profile="# profile\n",
    )
    assert outcome.verdict == Verdict.READY
    refreshed = storage.get_session(s.id)
    assert refreshed.stage == Stage.CLOSING
    assert refreshed.verdict == Verdict.READY


@pytest.mark.asyncio
async def test_final_gate_intent_fail_blocks_close(session_setup):
    storage, s, admin, _ = session_setup
    (Path(s.fs_path) / "normalized.md").write_text("draft\n", encoding="utf-8")
    (Path(s.fs_path) / "final" / "revised.md").write_text("revised\n", encoding="utf-8")
    storage.update_session(s.id, subject="t")

    fake = FakeLLMClient()
    fake.script("deepseek-v4-pro", json.dumps({
        "verdict": "FAIL", "csw_gate_status": "fail",
        "pillar_verdict": {"Background": "pass", "Materials": "pass",
                           "Framework": "pass", "Intent": "fail"},
        "regressions": ["p1"],
    }))
    outcome = await final_gate.run(
        storage=storage, llm=fake, model="deepseek-v4-pro",
        session=storage.get_session(s.id), responder_user=admin,
        admin_style="t", review_rules="r", responder_profile="p",
    )
    assert outcome.verdict == Verdict.FAIL
    refreshed = storage.get_session(s.id)
    # FAIL keeps stage at final_gating (not transitioned to closing)
    assert refreshed.stage != Stage.CLOSING
    assert refreshed.fail_count == 1
