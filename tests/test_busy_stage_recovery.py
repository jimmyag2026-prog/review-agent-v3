"""Issue #5: when a long-running stage (scan / merge / etc.) is interrupted by
restart, dispatcher should detect-and-restart on the next inbound message
rather than dropping the user into a silent fall-through."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import json
import pytest

from review_agent.config import load as load_config
from review_agent.core.dispatcher import Dispatcher
from review_agent.core.enums import Role, Stage
from review_agent.core.models import User
from review_agent.lark.types import IncomingMessage
from review_agent.llm.fake import FakeLLMClient
from review_agent.pipeline.confirm_topic import _trim_subject
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


def _seed(storage):
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
    Path(s.fs_path, "normalized.md").write_text("draft", encoding="utf-8")
    storage.update_session(s.id, subject="X")
    return s, admin, req


def _msg(oid: str, text: str) -> IncomingMessage:
    return IncomingMessage(
        event_id=f"e{text}", sender_open_id=oid, chat_type="p2p",
        msg_type="text", content_raw="", content_text=text,
        chat_id="c", create_time="0", message_id="m",
    )


@pytest.mark.asyncio
async def test_scanning_no_llm_call_restarts_scan(tmp_storage):
    """Most important: session stuck at SCANNING with no scan_four_pillar
    LLM call yet → next user message should restart scan, not fall through."""
    s, admin, req = _seed(tmp_storage)
    tmp_storage.update_session(s.id, stage=Stage.SCANNING)

    dispatcher, llm, lark = _build(tmp_storage)
    # script the two scan responses
    llm.script("deepseek-v4-pro",
               '{"findings":[{"id":"p1","pillar":"Intent","severity":"BLOCKER",'
               '"issue":"i","suggest":"s","anchor":{"line_range":[1,1],"snippet":""}}]}',
               '{"findings":[]}',
               '问题: x\n建议: y')

    await dispatcher._handle_incoming(_msg(req.open_id, "anything"))

    # the "上次中断了" message should be sent
    sent_texts = [c.args[1] for c in lark.send_dm_text.call_args_list]
    assert any("打断" in t or "重新跑" in t for t in sent_texts), \
        f"expected interruption notice; got: {sent_texts}"
    # scan should have actually run (llm calls recorded)
    assert tmp_storage.has_llm_call_for_stage(s.id, "scan_four_pillar")


@pytest.mark.asyncio
async def test_scanning_with_llm_call_returns_busy(tmp_storage):
    """If scan LLM call is already recorded (in progress / done), don't restart;
    just nudge user it's busy."""
    s, _, req = _seed(tmp_storage)
    tmp_storage.update_session(s.id, stage=Stage.SCANNING)
    # simulate that scan already started by inserting a llm_calls row
    tmp_storage.log_llm_call(
        session_id=s.id, stage="scan_four_pillar", model="deepseek-v4-flash",
        prompt_tokens=1, completion_tokens=1, reasoning_tokens=0,
        cache_hit_tokens=0, latency_ms=5000, finish_reason="stop",
        ok=True, error=None,
    )

    dispatcher, _, lark = _build(tmp_storage)
    await dispatcher._handle_incoming(_msg(req.open_id, "anything"))

    sent_texts = [c.args[1] for c in lark.send_dm_text.call_args_list]
    assert any("挑刺" in t or "等" in t for t in sent_texts), \
        f"expected busy nudge; got: {sent_texts}"


@pytest.mark.asyncio
async def test_merging_recovery(tmp_storage):
    """Same pattern for MERGING stage."""
    s, _, req = _seed(tmp_storage)
    tmp_storage.update_session(s.id, stage=Stage.MERGING)
    dispatcher, llm, lark = _build(tmp_storage)
    llm.script("deepseek-v4-pro", "revised body")

    await dispatcher._handle_incoming(_msg(req.open_id, "?"))

    sent_texts = [c.args[1] for c in lark.send_dm_text.call_args_list]
    assert any("打断" in t or "重新跑" in t for t in sent_texts), f"got {sent_texts}"
    # merge_draft attempted (llm_calls increases)
    assert tmp_storage.has_llm_call_for_stage(s.id, "merge_draft")


def test_trim_subject_short_passthrough():
    assert _trim_subject("批准5万市场预算") == "批准5万市场预算"


def test_trim_subject_truncates_long():
    long = "活动名称：AI Agent Builder Meetup #1\n时间地点：5月17日深圳\n目标人群：开发者\n" * 5
    s = _trim_subject(long, max_chars=60)
    assert len(s) <= 60
    assert "\n" not in s


def test_trim_subject_first_sentence_wins():
    s = _trim_subject("批准 X 预算。后面是更多细节描述...")
    assert s == "批准 X 预算"


def test_trim_subject_empty_fallback():
    assert _trim_subject("") == "(untitled)"
    assert _trim_subject("   ") == "(untitled)"


def test_storage_has_llm_call_for_stage(tmp_storage, session_setup):
    storage, s, *_ = session_setup
    assert not storage.has_llm_call_for_stage(s.id, "scan_four_pillar")
    storage.log_llm_call(
        session_id=s.id, stage="scan_four_pillar", model="m",
        prompt_tokens=1, completion_tokens=1, reasoning_tokens=0,
        cache_hit_tokens=0, latency_ms=10, finish_reason="stop",
        ok=True, error=None,
    )
    assert storage.has_llm_call_for_stage(s.id, "scan_four_pillar")
    assert not storage.has_llm_call_for_stage(s.id, "merge_draft")
