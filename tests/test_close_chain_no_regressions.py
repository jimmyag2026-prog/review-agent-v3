"""Issue #8: final_gate FAIL with no actionable open BLOCKERs must escalate
to FORCED_PARTIAL + deliver, not deadlock at empty cursor."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from review_agent.config import load as load_config
from review_agent.core.dispatcher import Dispatcher
from review_agent.core.enums import (
    FindingSource,
    FindingStatus,
    Pillar,
    Role,
    SessionStatus,
    Severity,
    Verdict,
)
from review_agent.core.models import Anchor, Cursor, Finding, User
from review_agent.llm.fake import FakeLLMClient
from review_agent.pipeline.ingest_backends import FakeIngestBackend


def _build(storage):
    cfg = load_config()
    cfg.paths.db = storage.db_path
    cfg.paths.fs = str(storage.fs_root)
    llm = FakeLLMClient()
    lark = AsyncMock()
    lark.send_dm_text = AsyncMock(return_value="m")
    lark.send_dm_post = AsyncMock(return_value="m")
    lark.create_doc = AsyncMock(return_value={"document_id": "d"})
    lark.aclose = AsyncMock()
    return Dispatcher(cfg=cfg, storage=storage, llm=llm, lark=lark,
                      ingest_backends=[FakeIngestBackend()]), llm, lark


@pytest.mark.asyncio
async def test_final_gate_fail_with_no_open_blockers_forces_partial(tmp_storage):
    """Issue #8 regression: pre-fix this would deadlock at empty cursor."""
    admin = User(open_id="ou_a", display_name="Boss",
                 roles=[Role.ADMIN, Role.RESPONDER])
    req = User(open_id="ou_r", display_name="Req",
               roles=[Role.REQUESTER], pairing_responder_oid="ou_a")
    tmp_storage.upsert_user(admin)
    tmp_storage.upsert_user(req)

    s = tmp_storage.create_session(
        requester_oid=req.open_id, responder_oid=admin.open_id,
        admin_style="t", review_rules="r", responder_profile="p",
    )
    Path(s.fs_path, "normalized.md").write_text("draft", encoding="utf-8")
    tmp_storage.update_session(s.id, subject="X")

    # all findings ACCEPTED — none open
    for fid in ("p1", "p2"):
        f = Finding(
            id=fid, round=1, created_at="t",
            source=FindingSource.FOUR_PILLAR, pillar=Pillar.INTENT,
            severity=Severity.BLOCKER, issue=f"i{fid}", suggest="s",
            anchor=Anchor(snippet=""), status=FindingStatus.ACCEPTED,
            reply="ok",
        )
        tmp_storage.append_finding(s, f)
    tmp_storage.save_cursor(s, Cursor())

    dispatcher, llm, lark = _build(tmp_storage)
    # script: merge → gate (FAIL with empty regressions) → summary
    llm.script(
        "deepseek-v4-pro",
        "revised body",
        json.dumps({
            "verdict": "FAIL", "csw_gate_status": "fail",
            "pillar_verdict": {"Background": "pass", "Materials": "fail",
                                "Framework": "pass", "Intent": "fail"},
            "regressions": [],  # ← key: empty
        }),
        "# 会前简报 — X\n## 1. 议题摘要\n…",
    )

    await dispatcher._enqueue_close_chain(s.id, forced=False)

    refreshed = tmp_storage.get_session(s.id)
    # Pre-fix: would be stuck at QA_ACTIVE_REOPENED with empty cursor.
    # Post-fix: forced partial close path.
    assert refreshed.status == SessionStatus.CLOSED, \
        f"expected CLOSED, got status={refreshed.status} stage={refreshed.stage}"
    assert refreshed.verdict == Verdict.FORCED_PARTIAL
    # user got the friendly explanation
    sent = [c.args[1] for c in lark.send_dm_text.call_args_list]
    assert any("BLOCKER" in t or "整理" in t or "brief" in t for t in sent), \
        f"expected friendly DM, got: {sent}"
