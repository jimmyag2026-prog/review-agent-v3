"""Dispatcher routing — every msg_type lands in the right place,
no silent drops."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from review_agent.config import load as load_config
from review_agent.core.dispatcher import Dispatcher
from review_agent.core.enums import Role, SessionStatus, Stage
from review_agent.core.models import User
from review_agent.lark.types import IncomingMessage
from review_agent.llm.fake import FakeLLMClient
from review_agent.pipeline.ingest_backends import FakeIngestBackend


_CONFIRM_TOPIC_JSON = json.dumps({
    "candidates": [{"key": "a", "topic": "T"}],
    "im_message": "(a) T (pass) (custom)",
})


def _build(storage):
    cfg = load_config()
    cfg.paths.db = storage.db_path
    cfg.paths.fs = str(storage.fs_root)
    llm = FakeLLMClient()
    llm.script("deepseek-v4-flash", _CONFIRM_TOPIC_JSON)
    lark = AsyncMock()
    lark.send_dm_text = AsyncMock(return_value="m")
    lark.send_dm_post = AsyncMock(return_value="m")
    lark.create_doc = AsyncMock(return_value={"document_id": "d"})
    lark.get_user = AsyncMock(return_value={"name": "Tester"})
    lark.download_attachment = AsyncMock()
    lark.get_doc_raw = AsyncMock(return_value="lark doc body")
    lark.get_wiki_node = AsyncMock(return_value={"obj_token": "doc-id"})
    lark.aclose = AsyncMock()
    return Dispatcher(cfg=cfg, storage=storage, llm=llm, lark=lark,
                      ingest_backends=[FakeIngestBackend()]), lark


def _seed_users(storage):
    admin = User(open_id="ou_a", display_name="Boss",
                 roles=[Role.ADMIN, Role.RESPONDER])
    req = User(open_id="ou_r", display_name="Req",
               roles=[Role.REQUESTER], pairing_responder_oid="ou_a")
    storage.upsert_user(admin)
    storage.upsert_user(req)


def _msg(*, msg_type: str, text: str = "", file_key: str = "",
         content_raw: str = "", oid: str = "ou_r") -> IncomingMessage:
    return IncomingMessage(
        event_id=f"e-{msg_type}", sender_open_id=oid, chat_type="p2p",
        msg_type=msg_type, content_raw=content_raw or "{}",
        content_text=text, chat_id="c", create_time="0", message_id="m",
        file_key=file_key,
    )


@pytest.mark.asyncio
async def test_text_no_url_creates_session(tmp_storage):
    """v3.2 Phase A: ingest goes through AWAITING_MATERIAL_CONFIRM gate first."""
    _seed_users(tmp_storage)
    dispatcher, lark = _build(tmp_storage)
    await dispatcher._handle_incoming(_msg(
        msg_type="text", text="想下周二批准 5 万市场预算给 X 项目",
    ))
    s = tmp_storage.list_sessions(requester_oid="ou_r")[0]
    assert s.status == SessionStatus.ACTIVE
    assert s.stage == Stage.AWAITING_MATERIAL_CONFIRM
    assert (Path(s.fs_path) / "normalized.md").exists()
    sent = [c.args[1] for c in lark.send_dm_text.call_args_list]
    assert any("我读到了" in t or "材料" in t for t in sent), \
        f"expected material-confirm DM; got: {sent}"


@pytest.mark.asyncio
async def test_text_with_lark_url_uses_lark_doc_backend(tmp_storage):
    _seed_users(tmp_storage)
    dispatcher, lark = _build(tmp_storage)
    await dispatcher._handle_incoming(_msg(
        msg_type="text",
        text="先看这个 https://acme.feishu.cn/docx/Tabc123 然后讨论",
    ))
    lark.get_doc_raw.assert_awaited_once_with("Tabc123")
    s = tmp_storage.list_sessions(requester_oid="ou_r")[0]
    body = (Path(s.fs_path) / "normalized.md").read_text()
    assert "lark doc body" in body
    assert "Lark 文档" in body


@pytest.mark.asyncio
async def test_text_with_web_url_calls_scrape(tmp_storage):
    _seed_users(tmp_storage)
    dispatcher, lark = _build(tmp_storage)

    with patch(
        "review_agent.pipeline.ingest_backends.web_scrape.WebScrapBackend.scrape_urls",
        new_callable=AsyncMock,
    ) as mock_scrape:
        from review_agent.pipeline.ingest_backends.base import IngestResult
        mock_scrape.return_value = IngestResult(
            backend="web_scrape", normalized="[🌐 scraped]\n\nbody", note="ok",
        )
        await dispatcher._handle_incoming(_msg(
            msg_type="text", text="see https://example.com/blog/post-x for ref",
        ))
        mock_scrape.assert_awaited_once()
        called_urls = mock_scrape.call_args.args[0]
        assert "https://example.com/blog/post-x" in called_urls

    s = tmp_storage.list_sessions(requester_oid="ou_r")[0]
    body = (Path(s.fs_path) / "normalized.md").read_text()
    assert "[🌐" in body


@pytest.mark.asyncio
async def test_post_msg_treated_as_text(tmp_storage):
    """Lark `post` (rich text) — content_text was extracted by router; dispatcher treats as text."""
    _seed_users(tmp_storage)
    dispatcher, lark = _build(tmp_storage)
    await dispatcher._handle_incoming(_msg(
        msg_type="post",
        text="提案 v1\n要不要给客户提供退款保障？\n预计成本：每月 5 万",
    ))
    s = tmp_storage.list_sessions(requester_oid="ou_r")[0]
    # v3.2 Phase A: ingest stops at AWAITING_MATERIAL_CONFIRM, not SUBJECT_CONFIRMATION
    assert s.stage == Stage.AWAITING_MATERIAL_CONFIRM


@pytest.mark.asyncio
async def test_image_downloads_and_ingests(tmp_storage):
    _seed_users(tmp_storage)
    dispatcher, lark = _build(tmp_storage)
    # PNG magic bytes so dispatcher writes .png ext
    lark.download_attachment = AsyncMock(
        return_value=(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, "img_v2_x", "image/png"),
    )
    await dispatcher._handle_incoming(_msg(
        msg_type="image", file_key="img_v2_x",
    ))
    lark.download_attachment.assert_awaited_once()
    s = tmp_storage.list_sessions(requester_oid="ou_r")[0]
    inputs = list((Path(s.fs_path) / "input").glob("*.png"))
    assert len(inputs) == 1


@pytest.mark.asyncio
async def test_audio_downloads_and_ingests(tmp_storage):
    _seed_users(tmp_storage)
    dispatcher, lark = _build(tmp_storage)
    lark.download_attachment = AsyncMock(
        return_value=(b"OggS" + b"\x00" * 100, "file_x", "audio/ogg"),
    )
    await dispatcher._handle_incoming(_msg(
        msg_type="audio", file_key="file_x",
    ))
    s = tmp_storage.list_sessions(requester_oid="ou_r")[0]
    inputs = list((Path(s.fs_path) / "input").glob("*.ogg"))
    assert len(inputs) == 1


@pytest.mark.asyncio
async def test_file_pdf_ingests(tmp_storage):
    _seed_users(tmp_storage)
    dispatcher, lark = _build(tmp_storage)
    pdf_bytes = b"%PDF-1.4\n%abc\n" + b"\x00" * 100
    lark.download_attachment = AsyncMock(return_value=(pdf_bytes, "key", "application/pdf"))
    await dispatcher._handle_incoming(_msg(
        msg_type="file", file_key="key",
        content_raw='{"file_name":"proposal.pdf"}',
    ))
    s = tmp_storage.list_sessions(requester_oid="ou_r")[0]
    inputs = list((Path(s.fs_path) / "input").glob("*.pdf"))
    assert len(inputs) == 1


@pytest.mark.asyncio
async def test_video_polite_refuse(tmp_storage):
    _seed_users(tmp_storage)
    dispatcher, lark = _build(tmp_storage)
    await dispatcher._handle_incoming(_msg(msg_type="media", file_key="vid"))
    sent = [c.args[1] for c in lark.send_dm_text.call_args_list]
    assert any("视频" in s or "处理" in s for s in sent)
    # session created and then cancelled
    sessions = tmp_storage.list_sessions(requester_oid="ou_r")
    assert sessions[0].status == SessionStatus.CANCELLED


@pytest.mark.asyncio
async def test_sticker_polite_refuse(tmp_storage):
    _seed_users(tmp_storage)
    dispatcher, lark = _build(tmp_storage)
    await dispatcher._handle_incoming(_msg(msg_type="sticker"))
    sent = [c.args[1] for c in lark.send_dm_text.call_args_list]
    assert any("表情" in s or "实质" in s for s in sent)


@pytest.mark.asyncio
async def test_share_chat_polite_refuse(tmp_storage):
    _seed_users(tmp_storage)
    dispatcher, lark = _build(tmp_storage)
    await dispatcher._handle_incoming(_msg(msg_type="share_chat"))
    sent = [c.args[1] for c in lark.send_dm_text.call_args_list]
    assert any("review" in s.lower() or "材料" in s for s in sent)


@pytest.mark.asyncio
async def test_ingest_rejected_friendly_dm_no_failed_state(tmp_storage):
    """B8: IngestRejected → friendly DM + cancel; do NOT mark as failed."""
    _seed_users(tmp_storage)
    dispatcher, lark = _build(tmp_storage)

    from review_agent.pipeline.ingest_backends.base import IngestRejected
    with patch.object(
        dispatcher.ingest, "run", side_effect=IngestRejected("PDF 解析没装"),
    ):
        await dispatcher._handle_incoming(_msg(
            msg_type="text", text="some text triggers ingest",
        ))

    s = tmp_storage.list_sessions(requester_oid="ou_r")[0]
    assert s.status == SessionStatus.CANCELLED
    assert s.stage == Stage.INGEST_FAILED
    sent = [c.args[1] for c in lark.send_dm_text.call_args_list]
    assert any("PDF 解析没装" in t for t in sent)
