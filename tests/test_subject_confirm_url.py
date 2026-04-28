"""Issue #9: at SUBJECT_CONFIRMATION, if Requester replies with a Lark URL
(or other URL / long text), treat as new material — re-ingest + re-run
confirm_topic — instead of jamming the URL into session.subject."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from review_agent.config import load as load_config
from review_agent.core.dispatcher import Dispatcher
from review_agent.core.enums import Role, Stage
from review_agent.core.models import User
from review_agent.lark.types import IncomingMessage
from review_agent.llm.fake import FakeLLMClient
from review_agent.pipeline.ingest_backends import FakeIngestBackend


_NEW_CONFIRM_JSON = json.dumps({
    "candidates": [{"key": "a", "topic": "Astros 大使激励是否合理"}],
    "im_message": "重新理解：(a) Astros 大使激励是否合理 (pass) (custom)",
})


def _build(storage):
    cfg = load_config()
    cfg.paths.db = storage.db_path
    cfg.paths.fs = str(storage.fs_root)
    llm = FakeLLMClient()
    llm.script("deepseek-v4-flash", _NEW_CONFIRM_JSON)
    lark = AsyncMock()
    lark.send_dm_text = AsyncMock(return_value="m")
    lark.send_dm_post = AsyncMock(return_value="m")
    lark.create_doc = AsyncMock(return_value={"document_id": "d"})
    lark.get_user = AsyncMock(return_value={"name": "Tester"})
    lark.get_doc_raw = AsyncMock(return_value="Astros 大使激励机制：每月 1 万额度奖励...")
    lark.get_wiki_node = AsyncMock(return_value={"obj_token": "doc-real-id"})
    lark.aclose = AsyncMock()
    return Dispatcher(cfg=cfg, storage=storage, llm=llm, lark=lark,
                      ingest_backends=[FakeIngestBackend()]), llm, lark


def _seed_session_at_subject_confirmation(storage):
    admin = User(open_id="ou_a", display_name="Boss",
                 roles=[Role.ADMIN, Role.RESPONDER])
    req = User(open_id="ou_r", display_name="Req",
               roles=[Role.REQUESTER], pairing_responder_oid="ou_a")
    storage.upsert_user(admin)
    storage.upsert_user(req)
    s = storage.create_session(
        requester_oid=req.open_id, responder_oid=admin.open_id,
        admin_style="t", review_rules="r", responder_profile="p",
    )
    Path(s.fs_path, "normalized.md").write_text("hello", encoding="utf-8")
    storage.update_session(
        s.id, stage=Stage.SUBJECT_CONFIRMATION,
        meta={"topic_candidates": [{"key": "a", "topic": "你好的招呼"}]},
    )
    return s, admin, req


def _msg(text: str, oid: str = "ou_r") -> IncomingMessage:
    return IncomingMessage(
        event_id=f"e-{hash(text)}", sender_open_id=oid, chat_type="p2p",
        msg_type="text", content_raw=json.dumps({"text": text}),
        content_text=text, chat_id="c", create_time="0", message_id="m",
    )


@pytest.mark.asyncio
async def test_lark_url_reply_at_subject_confirmation_reingests(tmp_storage):
    """Issue #9 regression: previously this URL got truncated to 60 chars and
    crammed into session.subject. After fix: bot fetches the doc + re-runs
    confirm_topic with the fresh content."""
    s, admin, req = _seed_session_at_subject_confirmation(tmp_storage)
    dispatcher, llm, lark = _build(tmp_storage)

    text = (
        "https://jsg8iy06jkpz.sg.larksuite.com/wiki/YcSPwKWUni2yahk8t6DlEaCmg4c\n\n"
        "这个是我写的Astros大使激励机制，请帮我看看如何"
    )
    await dispatcher._handle_incoming(_msg(text))

    # Lark Open API was called to fetch the wiki doc
    lark.get_wiki_node.assert_awaited_once_with("YcSPwKWUni2yahk8t6DlEaCmg4c")
    lark.get_doc_raw.assert_awaited_once_with("doc-real-id")

    # normalized.md replaced with fetched content (no longer "hello")
    norm = Path(s.fs_path, "normalized.md").read_text()
    assert "hello" not in norm
    assert "Astros 大使激励机制" in norm

    # session subject was NOT set to the URL string
    refreshed = tmp_storage.get_session(s.id)
    assert refreshed.subject != text
    assert refreshed.subject is None or "https://" not in (refreshed.subject or "")

    # bot DM'd Tester acknowledging re-ingest + sent new confirm_topic candidates
    sent = [c.args[1] for c in lark.send_dm_text.call_args_list]
    assert any("Lark 文档" in t or "重新分析" in t for t in sent), \
        f"expected re-ingest notice; got: {sent}"


@pytest.mark.asyncio
async def test_short_custom_text_still_works_as_subject(tmp_storage):
    """Sanity: a short custom answer (<300 chars, no URL) still sets subject normally."""
    s, _, _ = _seed_session_at_subject_confirmation(tmp_storage)
    dispatcher, llm, lark = _build(tmp_storage)
    # script the scan call this path triggers
    llm.script("deepseek-v4-pro",
               '{"findings":[{"id":"p1","pillar":"Intent","severity":"BLOCKER",'
               '"issue":"i","suggest":"s","anchor":{"line_range":[1,1],"snippet":""}}]}',
               '{"findings":[]}', "问题: x\n建议: y")

    await dispatcher._handle_incoming(_msg("custom 我要讨论 X 项目预算"))

    refreshed = tmp_storage.get_session(s.id)
    assert refreshed.subject and "X 项目预算" in refreshed.subject
    assert refreshed.stage == Stage.QA_ACTIVE  # progressed past confirm + scan


@pytest.mark.asyncio
async def test_pick_a_still_works(tmp_storage):
    """Sanity: a/b/c selections still work even after the URL gate."""
    s, _, _ = _seed_session_at_subject_confirmation(tmp_storage)
    dispatcher, llm, lark = _build(tmp_storage)
    llm.script("deepseek-v4-pro",
               '{"findings":[]}', '{"findings":[]}')  # empty scan

    await dispatcher._handle_incoming(_msg("a"))

    refreshed = tmp_storage.get_session(s.id)
    assert refreshed.subject == "你好的招呼"
    assert refreshed.stage in (Stage.QA_ACTIVE, Stage.SCANNING)
