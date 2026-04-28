"""Issue #2: dispatcher auto-registers unknown senders as Requester."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from review_agent.config import load as load_config
from review_agent.core.dispatcher import Dispatcher
from review_agent.core.enums import Role
from review_agent.core.models import User
from review_agent.lark.types import IncomingMessage
from review_agent.llm.fake import FakeLLMClient
from review_agent.pipeline.ingest_backends import FakeIngestBackend


_CONFIRM_TOPIC_JSON = """{
  "candidates": [
    {"key":"a","topic":"批准 X 预算"},
    {"key":"b","topic":"延后到下季度"}
  ],
  "im_message": "想做哪个决定？(a) 批准 (b) 延后 (pass) 跳 (custom) 其他"
}"""


def _build(storage):
    cfg = load_config()
    cfg.paths.db = storage.db_path
    cfg.paths.fs = str(storage.fs_root)
    llm = FakeLLMClient()
    # confirm_topic uses fast_model; pre-script a valid JSON envelope so the
    # post-register session-creation path doesn't blow up
    llm.script("deepseek-v4-flash", _CONFIRM_TOPIC_JSON)
    lark = AsyncMock()
    lark.send_dm_text = AsyncMock(return_value="msg_id")
    lark.get_user = AsyncMock(return_value={"name": "Alice"})
    lark.create_doc = AsyncMock(return_value={"document_id": "d1"})
    lark.aclose = AsyncMock()
    return Dispatcher(cfg=cfg, storage=storage, llm=llm, lark=lark,
                      ingest_backends=[FakeIngestBackend()]), lark


@pytest.mark.asyncio
async def test_auto_register_creates_requester_paired_with_admin(tmp_storage):
    admin = User(open_id="ou_admin", display_name="Boss",
                 roles=[Role.ADMIN, Role.RESPONDER])
    tmp_storage.upsert_user(admin)
    dispatcher, lark = _build(tmp_storage)

    msg = IncomingMessage(
        event_id="e1", sender_open_id="ou_stranger", chat_type="p2p",
        msg_type="text", content_raw="", content_text="hi",
        chat_id="c", create_time="0", message_id="m",
    )
    await dispatcher._handle_incoming(msg)

    new = tmp_storage.get_user("ou_stranger")
    assert new is not None
    assert Role.REQUESTER in new.roles
    assert new.pairing_responder_oid == "ou_admin"
    assert new.display_name == "Alice"  # picked up from lark.get_user mock

    # welcome DM to new user + notification to admin
    sent_targets = [c.args[0] for c in lark.send_dm_text.call_args_list]
    assert "ou_stranger" in sent_targets
    assert "ou_admin" in sent_targets


@pytest.mark.asyncio
async def test_auto_register_falls_back_when_no_lark_name(tmp_storage):
    admin = User(open_id="ou_admin", display_name="Boss",
                 roles=[Role.ADMIN, Role.RESPONDER])
    tmp_storage.upsert_user(admin)
    dispatcher, lark = _build(tmp_storage)
    lark.get_user = AsyncMock(side_effect=Exception("scope missing"))

    msg = IncomingMessage(
        event_id="e2", sender_open_id="ou_stranger2", chat_type="p2p",
        msg_type="text", content_raw="", content_text="hi",
        chat_id="c", create_time="0", message_id="m",
    )
    await dispatcher._handle_incoming(msg)

    new = tmp_storage.get_user("ou_stranger2")
    assert new is not None
    assert new.display_name == "New user"


@pytest.mark.asyncio
async def test_no_admin_refuses(tmp_storage):
    """If no Admin exists, refuse to auto-register (don't open the bot to the world)."""
    dispatcher, lark = _build(tmp_storage)

    msg = IncomingMessage(
        event_id="e3", sender_open_id="ou_stranger3", chat_type="p2p",
        msg_type="text", content_raw="", content_text="hi",
        chat_id="c", create_time="0", message_id="m",
    )
    await dispatcher._handle_incoming(msg)

    assert tmp_storage.get_user("ou_stranger3") is None
    sent_targets = [c.args[0] for c in lark.send_dm_text.call_args_list]
    assert sent_targets == ["ou_stranger3"]  # only the polite refuse, no admin notify


@pytest.mark.asyncio
async def test_disabled_flag_refuses(tmp_storage):
    admin = User(open_id="ou_admin", display_name="Boss",
                 roles=[Role.ADMIN, Role.RESPONDER])
    tmp_storage.upsert_user(admin)
    dispatcher, lark = _build(tmp_storage)
    dispatcher.cfg.review.auto_register_requesters = False

    msg = IncomingMessage(
        event_id="e4", sender_open_id="ou_stranger4", chat_type="p2p",
        msg_type="text", content_raw="", content_text="hi",
        chat_id="c", create_time="0", message_id="m",
    )
    await dispatcher._handle_incoming(msg)

    assert tmp_storage.get_user("ou_stranger4") is None


def test_storage_delete_user(tmp_storage):
    u = User(open_id="ou_x", display_name="X", roles=[Role.REQUESTER])
    tmp_storage.upsert_user(u)
    assert tmp_storage.delete_user("ou_x") is True
    assert tmp_storage.get_user("ou_x") is None
    assert tmp_storage.delete_user("ou_x") is False
