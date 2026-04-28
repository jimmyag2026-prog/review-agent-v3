"""Issue #4: AWAITING_CLOSE_CONFIRMATION state handler — covers the bug where
session got stuck after all BLOCKERs were accepted."""
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
    Severity,
    SessionStatus,
    Stage,
)
from review_agent.core.models import Anchor, Cursor, Finding, User
from review_agent.lark.types import IncomingMessage
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
    lark.get_user = AsyncMock(return_value={"name": "n"})
    lark.aclose = AsyncMock()
    return Dispatcher(cfg=cfg, storage=storage, llm=llm, lark=lark,
                      ingest_backends=[FakeIngestBackend()]), llm, lark


def _seed(storage, oid_admin="ou_a", oid_req="ou_r", *, deferred=()):
    admin = User(open_id=oid_admin, display_name="Boss",
                 roles=[Role.ADMIN, Role.RESPONDER])
    req = User(open_id=oid_req, display_name="Req",
               roles=[Role.REQUESTER], pairing_responder_oid=oid_admin)
    storage.upsert_user(admin)
    storage.upsert_user(req)
    s = storage.create_session(
        requester_oid=oid_req, responder_oid=oid_admin,
        admin_style="t", review_rules="r", responder_profile="p",
    )
    Path(s.fs_path, "normalized.md").write_text("draft", encoding="utf-8")
    storage.update_session(s.id, subject="X", stage=Stage.QA_ACTIVE)
    # seed accepted findings + deferred
    for fid in ("p1", "r1"):
        f = Finding(id=fid, round=1, created_at="t",
                    source=FindingSource.FOUR_PILLAR, pillar=Pillar.INTENT,
                    severity=Severity.BLOCKER, issue=fid, suggest="s",
                    anchor=Anchor(snippet=""), status=FindingStatus.ACCEPTED)
        storage.append_finding(s, f)
    for fid in deferred:
        f = Finding(id=fid, round=1, created_at="t",
                    source=FindingSource.FOUR_PILLAR, pillar=Pillar.MATERIALS,
                    severity=Severity.IMPROVEMENT, issue=fid, suggest="s",
                    anchor=Anchor(snippet=""))
        storage.append_finding(s, f)
    return s, admin, req


def _msg(oid: str, text: str) -> IncomingMessage:
    return IncomingMessage(
        event_id=f"e{text}", sender_open_id=oid, chat_type="p2p",
        msg_type="text", content_raw="", content_text=text,
        chat_id="c", create_time="0", message_id="m",
    )


@pytest.mark.asyncio
async def test_propose_close_transitions_to_awaiting_confirmation(tmp_storage):
    """When the LAST BLOCKER is accepted, propose_close fires + stage moves
    to AWAITING_CLOSE_CONFIRMATION (so subsequent replies are handled in
    that context, not no_op'd as in the live bug)."""
    s, admin, req = _seed(tmp_storage, deferred=("p2", "p3"))
    # cursor: current=r1 (last BLOCKER, no pending), deferred has IMPROVEMENTs
    tmp_storage.save_cursor(s, Cursor(current_id="r1", pending=[],
                                       deferred=["p2", "p3"], done=["p1"]))
    dispatcher, _, lark = _build(tmp_storage)

    await dispatcher._handle_incoming(_msg(req.open_id, "a"))

    refreshed = tmp_storage.get_session(s.id)
    assert refreshed.stage == Stage.AWAITING_CLOSE_CONFIRMATION
    assert lark.send_dm_text.called
    sent = lark.send_dm_text.call_args_list[-1].args[1]
    assert "BLOCKER" in sent or "close" in sent or "deferred" in sent


@pytest.mark.asyncio
async def test_close_confirmation_a_triggers_close(tmp_storage):
    s, admin, req = _seed(tmp_storage, deferred=("p2",))
    tmp_storage.save_cursor(s, Cursor(deferred=["p2"], done=["p1", "r1"]))
    tmp_storage.update_session(s.id, stage=Stage.AWAITING_CLOSE_CONFIRMATION)

    dispatcher, llm, lark = _build(tmp_storage)
    # script close-chain LLMs (merge / gate / summary)
    llm.script(
        "deepseek-v4-pro",
        "revised",
        json.dumps({"verdict": "READY", "csw_gate_status": "pass",
                    "pillar_verdict": {"Background": "pass", "Materials": "pass",
                                       "Framework": "pass", "Intent": "pass"},
                    "regressions": []}),
        "# 会前简报 — X\n## 1. 议题摘要\n…",
    )

    await dispatcher._handle_incoming(_msg(req.open_id, "a"))

    refreshed = tmp_storage.get_session(s.id)
    assert refreshed.status == SessionStatus.CLOSED


@pytest.mark.asyncio
async def test_close_confirmation_more_pulls_deferred(tmp_storage):
    s, admin, req = _seed(tmp_storage, deferred=("p2", "p3", "p4"))
    tmp_storage.save_cursor(s, Cursor(deferred=["p2", "p3", "p4"], done=["p1", "r1"]))
    tmp_storage.update_session(s.id, stage=Stage.AWAITING_CLOSE_CONFIRMATION)

    dispatcher, llm, lark = _build(tmp_storage)
    # qa_emit_finding will be called on the new current finding
    llm.script("deepseek-v4-pro", "问题: x\n建议: y")

    await dispatcher._handle_incoming(_msg(req.open_id, "more"))

    refreshed = tmp_storage.get_session(s.id)
    assert refreshed.stage == Stage.QA_ACTIVE
    cur = tmp_storage.load_cursor(refreshed)
    assert cur.current_id == "p2"
    # one DM ("📥 拉了 N 条…") + the rich-post finding
    assert lark.send_dm_text.called
    assert lark.send_dm_post.called


@pytest.mark.asyncio
async def test_close_confirmation_unknown_reply_nudges(tmp_storage):
    s, admin, req = _seed(tmp_storage)
    tmp_storage.save_cursor(s, Cursor(done=["p1", "r1"]))
    tmp_storage.update_session(s.id, stage=Stage.AWAITING_CLOSE_CONFIRMATION)

    dispatcher, _, lark = _build(tmp_storage)
    await dispatcher._handle_incoming(_msg(req.open_id, "发过去了吗"))

    # session shouldn't move and bot should reply with options nudge
    refreshed = tmp_storage.get_session(s.id)
    assert refreshed.stage == Stage.AWAITING_CLOSE_CONFIRMATION
    sent = lark.send_dm_text.call_args_list[-1].args[1]
    assert "a" in sent and "more" in sent  # nudge mentions options
