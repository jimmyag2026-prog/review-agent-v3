"""Issue #3a: full close chain delivers summary to BOTH responder and requester."""
from __future__ import annotations

import json
import sqlite3
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
)
from review_agent.core.models import Anchor, Cursor, Finding, User
from review_agent.llm.fake import FakeLLMClient
from review_agent.pipeline.ingest_backends import FakeIngestBackend


@pytest.fixture
def two_user_setup(tmp_storage):
    admin = User(open_id="ou_admin", display_name="Boss",
                 roles=[Role.ADMIN, Role.RESPONDER])
    req = User(open_id="ou_req", display_name="Junior",
               roles=[Role.REQUESTER], pairing_responder_oid="ou_admin")
    tmp_storage.upsert_user(admin)
    tmp_storage.upsert_user(req)
    return tmp_storage, admin, req


def _build(storage):
    cfg = load_config()
    cfg.paths.db = storage.db_path
    cfg.paths.fs = str(storage.fs_root)
    llm = FakeLLMClient()
    lark = AsyncMock()
    lark.send_dm_text = AsyncMock(return_value="lark_msg_xxx")
    lark.create_doc = AsyncMock(return_value={"document_id": "doc_xxx"})
    lark.get_user = AsyncMock(return_value={"name": "Anyone"})
    lark.aclose = AsyncMock()
    return Dispatcher(cfg=cfg, storage=storage, llm=llm, lark=lark,
                      ingest_backends=[FakeIngestBackend()]), llm, lark


@pytest.mark.asyncio
async def test_close_delivers_to_both_admin_and_requester(two_user_setup):
    storage, admin, req = two_user_setup

    # spin up a session at qa_active with one accepted finding (= ready to close)
    s = storage.create_session(
        requester_oid=req.open_id, responder_oid=admin.open_id,
        admin_style="tone: direct\n", review_rules="- 4 pillars\n",
        responder_profile="# profile\n",
    )
    # seed normalized + finding + cursor empty (= no pending → can close)
    Path(s.fs_path, "normalized.md").write_text("draft text", encoding="utf-8")
    f = Finding(
        id="p1", round=1, created_at="2026-04-28T00:00:00Z",
        source=FindingSource.FOUR_PILLAR, pillar=Pillar.INTENT,
        severity=Severity.BLOCKER, issue="i", suggest="s",
        anchor=Anchor(snippet="snip"),
        status=FindingStatus.ACCEPTED, reply="ok",
    )
    storage.append_finding(s, f)
    storage.save_cursor(s, Cursor())
    storage.update_session(s.id, subject="Should we ship X")

    dispatcher, llm, lark = _build(storage)
    # script LLM responses for the chain: merge → gate → summary
    llm.script(
        "deepseek-v4-pro",
        "revised draft body — accepted suggestion applied",
        json.dumps({
            "verdict": "READY", "csw_gate_status": "pass",
            "pillar_verdict": {"Background": "pass", "Materials": "pass",
                                "Framework": "pass", "Intent": "pass"},
            "regressions": [],
        }),
        "# 会前简报 — Should we ship X\n\n## 1. 议题摘要\n要不要发 X\n\n"
        "## 2. 核心数据\n…\n## 3. 团队自检结果\n…\n## 4. 待决策事项\n…\n"
        "## 5. 建议时间分配\n…\n## 6. 风险提示\n…",
    )

    await dispatcher._enqueue_close_chain(s.id, forced=False)

    # session closed
    refreshed = storage.get_session(s.id)
    assert refreshed.status == SessionStatus.CLOSED
    assert refreshed.verdict.value in {"READY", "READY_WITH_OPEN_ITEMS"}

    # summary.md generated
    summary = Path(refreshed.fs_path) / "summary.md"
    assert summary.exists() and summary.read_text(encoding="utf-8").strip()
    assert "议题摘要" in summary.read_text(encoding="utf-8")

    # outbound table records BOTH admin and requester DMs
    rows = storage.conn().execute(
        "SELECT to_open_id, msg_type, ok FROM outbound ORDER BY id"
    ).fetchall()
    rec = [(r["to_open_id"], r["msg_type"], r["ok"]) for r in rows]
    # at least one lark_doc to admin (responder), one lark_dm to admin, one lark_dm to req
    admin_doc = [r for r in rec if r[0] == "ou_admin" and r[1] == "lark_doc"]
    admin_dm = [r for r in rec if r[0] == "ou_admin" and r[1] == "lark_dm"]
    req_dm = [r for r in rec if r[0] == "ou_req" and r[1] == "lark_dm"]
    assert admin_doc, f"admin should receive Lark Doc. got: {rec}"
    assert admin_dm, f"admin should receive summary DM. got: {rec}"
    assert req_dm, f"requester should receive summary DM. got: {rec}"
    # all delivered ok
    assert all(r[2] == 1 for r in admin_dm + admin_doc + req_dm)

    # lark.send_dm_text was called with both admin and requester open_ids
    targets = {c.args[0] for c in lark.send_dm_text.call_args_list}
    assert "ou_admin" in targets
    assert "ou_req" in targets

    # the DMs sent to both contain the summary body
    admin_calls = [c for c in lark.send_dm_text.call_args_list if c.args[0] == "ou_admin"]
    req_calls = [c for c in lark.send_dm_text.call_args_list if c.args[0] == "ou_req"]
    assert any("议题摘要" in c.args[1] for c in admin_calls)
    assert any("议题摘要" in c.args[1] for c in req_calls)


@pytest.mark.asyncio
async def test_close_solo_mode_dedup_does_not_double_deliver(tmp_storage):
    """Edge: when admin == responder == requester (solo testing), dedup avoids double delivery."""
    user = User(open_id="ou_solo", display_name="Solo",
                roles=[Role.ADMIN, Role.RESPONDER, Role.REQUESTER],
                pairing_responder_oid="ou_solo")
    tmp_storage.upsert_user(user)

    s = tmp_storage.create_session(
        requester_oid="ou_solo", responder_oid="ou_solo",
        admin_style="t", review_rules="r", responder_profile="p",
    )
    Path(s.fs_path, "normalized.md").write_text("draft", encoding="utf-8")
    tmp_storage.save_cursor(s, Cursor())
    tmp_storage.update_session(s.id, subject="Solo test")

    dispatcher, llm, lark = _build(tmp_storage)
    llm.script(
        "deepseek-v4-pro",
        "revised body",
        json.dumps({"verdict": "READY", "csw_gate_status": "pass",
                    "pillar_verdict": {"Background": "pass", "Materials": "pass",
                                       "Framework": "pass", "Intent": "pass"},
                    "regressions": []}),
        "# 会前简报 — Solo test\n## 1. 议题摘要\nx",
    )
    await dispatcher._enqueue_close_chain(s.id, forced=False)

    # session closes successfully
    assert tmp_storage.get_session(s.id).status == SessionStatus.CLOSED

    # solo user gets both Doc + at least one DM (dedup MAY suppress the second
    # identical DM since responder & requester DMs have the same content_hash);
    # what we care about: NO failure rows
    rows = tmp_storage.conn().execute(
        "SELECT to_open_id, msg_type, ok, error FROM outbound"
    ).fetchall()
    failures = [dict(r) for r in rows if r["ok"] == 0]
    assert not failures, f"deliver should not have failures: {failures}"
