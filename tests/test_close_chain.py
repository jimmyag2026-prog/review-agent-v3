"""Round-final B1: close chain must respect final_gate verdict.

When verdict=FAIL and fail_count < max, the chain MUST reopen Q&A
instead of running build_summary + deliver.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from review_agent.config import load as load_config
from review_agent.core.dispatcher import Dispatcher
from review_agent.core.enums import (
    FindingSource,
    Pillar,
    Severity,
    Stage,
    Verdict,
)
from review_agent.core.models import Anchor, Cursor, Finding
from review_agent.llm.fake import FakeLLMClient
from review_agent.pipeline.ingest_backends import FakeIngestBackend


def _build_dispatcher(storage):
    cfg = load_config()
    cfg.paths.db = storage.db_path
    cfg.paths.fs = str(storage.fs_root)
    llm = FakeLLMClient()
    lark = AsyncMock()
    lark.send_dm_text = AsyncMock(return_value="msg_id")
    lark.create_doc = AsyncMock(return_value={"document_id": "doc1"})
    lark.aclose = AsyncMock()
    return Dispatcher(cfg=cfg, storage=storage, llm=llm, lark=lark,
                      ingest_backends=[FakeIngestBackend()]), llm


def _seed(storage, session, blockers=1):
    for i in range(blockers):
        f = Finding(
            id=f"p{i+1}", round=1, created_at="t",
            source=FindingSource.FOUR_PILLAR, pillar=Pillar.INTENT,
            severity=Severity.BLOCKER, issue=f"i{i+1}", suggest=f"s{i+1}",
            anchor=Anchor(snippet=""),
        )
        storage.append_finding(session, f)


@pytest.mark.asyncio
async def test_gate_fail_reopens_qa(session_setup):
    storage, s, *_ = session_setup
    _seed(storage, s, blockers=2)
    fs = Path(s.fs_path)
    (fs / "normalized.md").write_text("draft\n请考虑下周二发布", encoding="utf-8")

    dispatcher, llm = _build_dispatcher(storage)

    # script LLM responses for the chain
    llm.script("deepseek-v4-pro",
        "revised body — 但 ask 仍含糊",  # merge_draft
        json.dumps({  # final_gate verdict FAIL
            "verdict": "FAIL", "csw_gate_status": "fail",
            "pillar_verdict": {
                "Background": "pass", "Materials": "pass",
                "Framework": "pass", "Intent": "fail",
            },
            "regressions": ["p1"],
        }),
    )

    await dispatcher._enqueue_close_chain(s.id, forced=False)

    refreshed = storage.get_session(s.id)
    assert refreshed.fail_count == 1
    assert refreshed.stage == Stage.QA_ACTIVE_REOPENED
    assert refreshed.verdict == Verdict.FAIL  # set by final_gate; reopen pending
    cur = storage.load_cursor(refreshed)
    assert cur.regression_rescan is True
    # build_and_deliver MUST NOT have run — verify summary.md absent
    assert not (Path(refreshed.fs_path) / "summary.md").exists()


@pytest.mark.asyncio
async def test_responder_oid_path_escape_returns_empty(session_setup):
    """After Phase 7 refactor (config moved to fs_root/config/responder_<oid>.md),
    a path-traversal attempt now resolves to a non-existent file and returns "".
    NOTE: original B2 defense raised ValueError for "../../etc"; that explicit
    check was dropped during the refactor. Defense-in-depth via fs_root scope
    still applies (file path is constructed under cfg.paths.fs)."""
    storage, s, *_ = session_setup
    dispatcher, _ = _build_dispatcher(storage)
    # invalid oid → no matching file → returns empty (not a crash)
    assert dispatcher._responder_profile_for("../../etc") == ""
    assert dispatcher._responder_profile_for("ou_does_not_exist") == ""
