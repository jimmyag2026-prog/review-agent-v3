"""v3.2 Phase A + B: pre-review confirmation gate + mid-review supplement."""
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
    Stage,
)
from review_agent.core.models import Anchor, Cursor, Finding, User
from review_agent.lark.types import IncomingMessage
from review_agent.llm.fake import FakeLLMClient
from review_agent.pipeline.ingest_backends import FakeIngestBackend


_CONFIRM_TOPIC_JSON = json.dumps({
    "candidates": [{"key": "a", "topic": "T"}],
    "im_message": "(a) T (pass) (custom)",
})

_SCAN_A = '{"findings":[{"id":"p1","pillar":"Intent","severity":"BLOCKER","issue":"i","suggest":"s","anchor":{"line_range":[1,1],"snippet":""}}]}'
_SCAN_B = '{"findings":[]}'


def _build(storage):
    cfg = load_config()
    cfg.paths.db = storage.db_path
    cfg.paths.fs = str(storage.fs_root)
    llm = FakeLLMClient()
    llm.script("deepseek-v4-flash", _CONFIRM_TOPIC_JSON)
    llm.script("deepseek-v4-pro", _SCAN_A, _SCAN_B, "问题: x\n建议: y")
    lark = AsyncMock()
    lark.send_dm_text = AsyncMock(return_value="m")
    lark.send_dm_post = AsyncMock(return_value="m")
    lark.create_doc = AsyncMock(return_value={"document_id": "d"})
    lark.get_user = AsyncMock(return_value={"name": "Tester"})
    lark.get_doc_raw = AsyncMock(return_value="lark doc body")
    lark.get_wiki_node = AsyncMock(return_value={"obj_token": "doc-id"})
    lark.aclose = AsyncMock()
    return Dispatcher(cfg=cfg, storage=storage, llm=llm, lark=lark,
                      ingest_backends=[FakeIngestBackend()]), llm, lark


def _seed_users(storage):
    admin = User(open_id="ou_a", display_name="Boss",
                 roles=[Role.ADMIN, Role.RESPONDER])
    req = User(open_id="ou_r", display_name="Req",
               roles=[Role.REQUESTER], pairing_responder_oid="ou_a")
    storage.upsert_user(admin)
    storage.upsert_user(req)


def _msg(*, text: str = "", msg_type: str = "text",
         file_key: str = "", oid: str = "ou_r") -> IncomingMessage:
    return IncomingMessage(
        event_id=f"e-{hash(text + msg_type)}", sender_open_id=oid, chat_type="p2p",
        msg_type=msg_type, content_raw="{}",
        content_text=text, chat_id="c", create_time="0", message_id="m",
        file_key=file_key,
    )


# ───────────────────────── Phase A ─────────────────────────


@pytest.mark.asyncio
async def test_first_text_lands_at_awaiting_material_confirm(tmp_storage):
    _seed_users(tmp_storage)
    dispatcher, llm, lark = _build(tmp_storage)
    await dispatcher._handle_incoming(_msg(
        text="想下周推一个 X 功能给团队 review，希望 Boss 批准 5 万市场预算"
    ))
    s = tmp_storage.list_sessions(requester_oid="ou_r")[0]
    assert s.stage == Stage.AWAITING_MATERIAL_CONFIRM
    sent = [c.args[1] for c in lark.send_dm_text.call_args_list]
    # bot showed preview + asked to confirm or supplement
    assert any("我读到了" in t for t in sent)
    assert any("ok" in t.lower() or "开始" in t for t in sent)


@pytest.mark.asyncio
async def test_ok_advances_to_subject_confirmation(tmp_storage):
    _seed_users(tmp_storage)
    dispatcher, llm, lark = _build(tmp_storage)
    # first message lands at AWAITING_MATERIAL_CONFIRM
    await dispatcher._handle_incoming(_msg(text="提案：X 功能 5 万预算"))
    # second message: "ok" should advance
    await dispatcher._handle_incoming(_msg(text="ok"))
    s = tmp_storage.list_sessions(requester_oid="ou_r")[0]
    assert s.stage == Stage.SUBJECT_CONFIRMATION


@pytest.mark.asyncio
async def test_supplement_at_material_confirm_appends_and_re_asks(tmp_storage):
    _seed_users(tmp_storage)
    dispatcher, llm, lark = _build(tmp_storage)
    # initial material
    await dispatcher._handle_incoming(_msg(text="提案：X 功能 5 万预算"))
    # supplementary URL → appends + re-shows preview
    await dispatcher._handle_incoming(_msg(
        text="补充背景 https://acme.feishu.cn/docx/Tabc123",
    ))
    s = tmp_storage.list_sessions(requester_oid="ou_r")[0]
    assert s.stage == Stage.AWAITING_MATERIAL_CONFIRM  # still in gate
    norm = (Path(s.fs_path) / "normalized.md").read_text()
    # FakeIngestBackend stamps "FIXTURE NORMALIZED CONTENT" for the initial
    # ingest — what matters is that BOTH the original ingest output and the
    # appended Lark doc body are present.
    assert "FIXTURE NORMALIZED CONTENT" in norm  # original kept
    assert "lark doc body" in norm  # appended
    assert "[补充材料]" in norm
    lark.get_doc_raw.assert_awaited_once_with("Tabc123")


@pytest.mark.asyncio
async def test_cancel_at_material_confirm_cancels_session(tmp_storage):
    _seed_users(tmp_storage)
    dispatcher, llm, lark = _build(tmp_storage)
    await dispatcher._handle_incoming(_msg(text="提案：X 功能"))
    await dispatcher._handle_incoming(_msg(text="cancel"))
    s = tmp_storage.list_sessions(requester_oid="ou_r")[0]
    assert s.status == SessionStatus.CANCELLED
    assert s.stage == Stage.CANCELLED


# ───────────────────────── Phase B ─────────────────────────


@pytest.mark.asyncio
async def test_supplement_during_qa_triggers_rescan(tmp_storage):
    """v3.2 Phase B: URL or long text mid-Q&A → append + reset cursor + rescan."""
    _seed_users(tmp_storage)
    dispatcher, llm, lark = _build(tmp_storage)

    # set up a session already at QA_ACTIVE with one finding cursor
    s = tmp_storage.create_session(
        requester_oid="ou_r", responder_oid="ou_a",
        admin_style="t", review_rules="r", responder_profile="p",
    )
    Path(s.fs_path, "normalized.md").write_text("初稿草案", encoding="utf-8")
    f = Finding(id="p1", round=1, created_at="t",
                source=FindingSource.FOUR_PILLAR, pillar=Pillar.INTENT,
                severity=Severity.BLOCKER, issue="i", suggest="s",
                anchor=Anchor(snippet=""))
    tmp_storage.append_finding(s, f)
    tmp_storage.save_cursor(s, Cursor(current_id="p1"))
    tmp_storage.update_session(s.id, subject="X", stage=Stage.QA_ACTIVE)

    # Tester sends a Lark URL mid-Q&A (not an a/b/c reply)
    pre_round = tmp_storage.get_session(s.id).round_no
    await dispatcher._handle_incoming(_msg(
        text="补充背景 https://acme.feishu.cn/docx/Tnew"
    ))

    refreshed = tmp_storage.get_session(s.id)
    norm = (Path(refreshed.fs_path) / "normalized.md").read_text()
    assert "初稿草案" in norm
    assert "lark doc body" in norm  # appended fetched content
    # round_no incremented (rescan)
    assert refreshed.round_no == pre_round + 1
    sent = [c.args[1] for c in lark.send_dm_text.call_args_list]
    assert any("补充材料" in t or "重新扫" in t for t in sent), \
        f"expected supplement notice; got: {sent}"


@pytest.mark.asyncio
async def test_short_qa_reply_still_goes_to_qa_loop(tmp_storage):
    """Sanity: a short 'a' reply during Q&A is NOT treated as supplement."""
    _seed_users(tmp_storage)
    dispatcher, llm, lark = _build(tmp_storage)

    s = tmp_storage.create_session(
        requester_oid="ou_r", responder_oid="ou_a",
        admin_style="t", review_rules="r", responder_profile="p",
    )
    Path(s.fs_path, "normalized.md").write_text("draft", encoding="utf-8")
    f = Finding(id="p1", round=1, created_at="t",
                source=FindingSource.FOUR_PILLAR, pillar=Pillar.INTENT,
                severity=Severity.BLOCKER, issue="i", suggest="s",
                anchor=Anchor(snippet=""))
    tmp_storage.append_finding(s, f)
    tmp_storage.save_cursor(s, Cursor(current_id="p1"))
    tmp_storage.update_session(s.id, subject="X", stage=Stage.QA_ACTIVE)

    pre_round = tmp_storage.get_session(s.id).round_no
    await dispatcher._handle_incoming(_msg(text="a"))

    # round_no should NOT have changed (no rescan), finding marked accepted
    refreshed = tmp_storage.get_session(s.id)
    assert refreshed.round_no == pre_round
    findings = tmp_storage.load_findings(refreshed)
    assert findings[0]["status"] == FindingStatus.ACCEPTED.value
