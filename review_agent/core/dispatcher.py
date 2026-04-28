"""Inbound event → handler routing.

Decides per-event whether to:
- politely refuse (unknown sender)
- start a new session (Requester with attachment / long text)
- continue an active session (Requester reply)
- run admin/responder commands

Handlers themselves are idempotent so the worker can retry without state damage.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Config
from ..core.enums import Intent, Role, SessionStatus, Stage
from ..core.models import Session, User
from ..core.storage import Storage
from ..lark.client import LarkClient
from ..lark.types import IncomingMessage
from ..llm.base import LLMClient, LLMTerminalFailure
from ..pipeline import build_summary, confirm_topic, deliver, final_gate, merge_draft, qa_loop, scan
from ..pipeline import _format
from ..pipeline._intents import parse_reply_intent
from ..pipeline._prompts import render
from ..pipeline.delivery_backends import (
    DeliveryBackend,
    LarkDmBackend,
    LarkDocBackend,
    LocalArchiveBackend,
)
from ..pipeline.ingest import IngestPipeline
from ..pipeline.ingest_backends import IngestBackend
from ..util import log
from ..util.ids import now_iso
from ..util.path import atomic_write, resolve_session_path

_logger = log.get(__name__)


class Dispatcher:
    def __init__(
        self,
        *,
        cfg: Config,
        storage: Storage,
        llm: LLMClient,
        lark: LarkClient,
        ingest_backends: list[IngestBackend],
    ):
        self.cfg = cfg
        self.storage = storage
        self.llm = llm
        self.lark = lark
        self.ingest = IngestPipeline(cfg.paths.fs, ingest_backends)
        self.delivery_backends: dict[str, DeliveryBackend] = {
            "lark_dm": LarkDmBackend(lark),
            "lark_doc": LarkDocBackend(lark),
            "local_path": LocalArchiveBackend(),
        }

    # ── entry point: a task envelope dequeued by worker ──
    async def dispatch(self, task: dict) -> None:
        kind = task["kind"]
        payload = task["payload"]
        if kind == "incoming_message":
            await self._handle_incoming(IncomingMessage(**payload))
        elif kind == "scan":
            await self._do_scan(payload["session_id"])
        elif kind == "merge_draft":
            await self._do_merge(payload["session_id"])
        elif kind == "final_gate":
            await self._do_final_gate(payload["session_id"], forced=payload.get("forced", False))
        elif kind == "build_and_deliver":
            await self._do_build_and_deliver(payload["session_id"])
        else:
            _logger.warning("unknown task kind: %s", kind)

    # ── inbound DM dispatch ─────────────────────────────
    async def _handle_incoming(self, msg: IncomingMessage) -> None:
        sender = self.storage.get_user(msg.sender_open_id)

        # Issue #2: auto-register unknown senders as Requester paired with
        # the (sole) admin's pairing Responder. Bypass with config flag.
        if sender is None:
            sender = await self._maybe_auto_register(msg)
            if sender is None:
                # auto-register declined (no admin set up yet, or flag off)
                return

        active = self.storage.get_active_session_for(sender.open_id)

        if Role.REQUESTER in sender.roles:
            if active:
                await self._handle_requester_in_session(sender, active, msg)
            else:
                await self._handle_requester_no_session(sender, msg)
            return

        # pure Admin / Responder (no Requester role): v0 just acknowledges
        await self._safe_dm(
            sender.open_id,
            f"Hi {sender.display_name}, admin/responder controls live in the CLI / dashboard."
            " I'll DM you summaries when sessions close.",
        )

    async def _maybe_auto_register(self, msg: IncomingMessage) -> User | None:
        """Auto-create a Requester for an unknown sender. Returns the new
        User if registration succeeded, None if we declined (caller must stop)."""
        if not self.cfg.review.auto_register_requesters:
            await self._safe_dm(
                msg.sender_open_id,
                "Hi, I'm review-agent. Auto-registration is disabled — ask the admin to add you.",
            )
            return None

        admins = self.storage.list_users(Role.ADMIN)
        if not admins:
            # no admin yet → cannot pair, refuse to avoid open-relay
            await self._safe_dm(
                msg.sender_open_id,
                "Hi, I'm review-agent. The admin hasn't finished setup yet. Try again later.",
            )
            _logger.warning("auto-register refused for %s: no admin in db", msg.sender_open_id)
            return None

        admin = admins[0]
        if Role.RESPONDER in admin.roles:
            responder_oid = admin.open_id
        else:
            responders = self.storage.list_users(Role.RESPONDER)
            responder_oid = responders[0].open_id if responders else admin.open_id

        display_name = await self._lookup_display_name(msg.sender_open_id) or "New user"

        new_user = User(
            open_id=msg.sender_open_id,
            display_name=display_name,
            roles=[Role.REQUESTER],
            pairing_responder_oid=responder_oid,
        )
        self.storage.upsert_user(new_user)
        _logger.info("auto-registered Requester %s (%s) → responder=%s",
                     msg.sender_open_id, display_name, responder_oid)

        # Issue #7: welcome with tutorial-style onboarding (no jargon)
        await self._safe_dm(
            msg.sender_open_id,
            _format.welcome_message(
                requester_name=display_name,
                responder_name=admin.display_name,
            ),
        )
        await self._safe_dm(
            admin.open_id,
            _format.admin_notify_message(
                requester_name=display_name,
                requester_oid=msg.sender_open_id,
            ),
        )
        return new_user

    async def _lookup_display_name(self, open_id: str) -> str | None:
        try:
            user = await self.lark.get_user(open_id)
            return user.get("name") or user.get("nick_name") or None
        except Exception as e:
            _logger.debug("lark.get_user(%s) failed: %s", open_id, e)
            return None

    async def _handle_requester_no_session(self, user: User, msg: IncomingMessage) -> None:
        responder = (
            self.storage.get_user(user.pairing_responder_oid)
            if user.pairing_responder_oid else None
        )
        if responder is None:
            await self._safe_dm(user.open_id, "你还没有绑定 Responder。让 admin 帮你设置一下。")
            return

        admin_style, review_rules = self._global_configs()
        responder_profile = self._responder_profile_for(responder.open_id)

        session = self.storage.create_session(
            requester_oid=user.open_id, responder_oid=responder.open_id,
            admin_style=admin_style, review_rules=review_rules,
            responder_profile=responder_profile,
        )

        # ── Phase 8: multimodal dispatch ───────────────────
        try:
            path = await self._save_and_ingest_multimodal(user, session, msg)
        except Exception as e:
            await self._fail_session(session, Stage.INGEST_FAILED, "ingest", e)
            return
        if path is None:
            return  # already sent a DM (unsupported / too large / etc.)

        self.storage.update_session(session.id, stage=Stage.SUBJECT_CONFIRMATION)
        await self._do_confirm_topic(session.id)

    async def _save_and_ingest_multimodal(
        self, user: User, session: Session, msg: IncomingMessage,
    ) -> Path | None:
        """Download, save, and ingest a message. Returns the saved file path
        on success, None if we sent a DM and the caller should stop."""
        fs_root = self.cfg.paths.fs
        iso = now_iso().replace(":", "")

        # ── Text message (existing path, unchanged) ──
        if msg.msg_type == "text" and msg.content_text.strip():
            text_path = resolve_session_path(
                fs_root, user.open_id, session.id,
                f"input/{iso}_text.md",
            )
            atomic_write(text_path, msg.content_text)
            await self.ingest.run(session, text_path.name)
            return text_path

        # ── Image message ──
        if msg.msg_type == "image" and msg.file_key:
            raw, _, _ = await self.lark.download_attachment(
                msg.message_id, msg.file_key, kind="image",
            )
            img_path = resolve_session_path(
                fs_root, user.open_id, session.id,
                f"input/{iso}_image.jpg",
            )
            img_path.write_bytes(raw)
            await self.ingest.run(session, img_path.name)
            return img_path

        # ── File message ──
        if msg.msg_type == "file" and msg.file_key:
            raw, _, _ = await self.lark.download_attachment(
                msg.message_id, msg.file_key, kind="file",
            )
            ext = self._guess_ext_from_content_raw(msg.content_raw)
            file_path = resolve_session_path(
                fs_root, user.open_id, session.id,
                f"input/{iso}_file{ext}",
            )
            file_path.write_bytes(raw)
            await self.ingest.run(session, file_path.name)
            return file_path

        # ── Audio message ──
        if msg.msg_type == "audio" and msg.file_key:
            raw, _, _ = await self.lark.download_attachment(
                msg.message_id, msg.file_key, kind="audio",
            )
            audio_path = resolve_session_path(
                fs_root, user.open_id, session.id,
                f"input/{iso}_audio.mp3",
            )
            audio_path.write_bytes(raw)
            await self.ingest.run(session, audio_path.name)
            return audio_path

        # ── URL detection in text messages ──
        if msg.msg_type == "text" and msg.content_text.strip():
            urls = _extract_urls_simple(msg.content_text)
            if urls:
                url_path = resolve_session_path(
                    fs_root, user.open_id, session.id,
                    f"input/{iso}_urls.txt",
                )
                atomic_write(url_path, "\n".join(urls))
                try:
                    await self.ingest.run(session, url_path.name)
                except Exception:
                    text_path = resolve_session_path(
                        fs_root, user.open_id, session.id,
                        f"input/{iso}_text.md",
                    )
                    atomic_write(text_path, msg.content_text)
                    await self.ingest.run(session, text_path.name)
                    return text_path
                return url_path

        await self._safe_dm(
            user.open_id,
            "收到。这个类型我还不会处理（v0 还不支持图片/文件/语音），能直接贴正文给我吗？",
        )
        return None

    @staticmethod
    def _guess_ext_from_content_raw(content_raw: str) -> str:
        import json
        try:
            parsed = json.loads(content_raw)
            fname = parsed.get("file_name", "")
            if fname:
                from pathlib import Path
                return Path(fname).suffix
        except (json.JSONDecodeError, Exception):
            pass
        return ".bin"

    async def _handle_requester_in_session(
        self, user: User, session: Session, msg: IncomingMessage
    ) -> None:
        if session.stage == Stage.SUBJECT_CONFIRMATION:
            intent, chosen = confirm_topic.handle_reply(
                storage=self.storage, session=session, reply=msg.content_text,
            )
            if chosen:
                await self._do_scan(session.id)
            else:
                await self._safe_dm(user.open_id,
                                    "我没听明白要 review 哪一个，再发一遍主题或选 a/b/c。")
            return

        if session.stage in (Stage.QA_ACTIVE, Stage.QA_ACTIVE_REOPENED):
            self.storage.update_session(session.id, stage=Stage.QA_ACTIVE)
            outcome = qa_loop.handle_reply(
                storage=self.storage, session=session, reply=msg.content_text,
                top_n_more=self.cfg.review.top_n_findings,
            )
            if outcome.action == "emit_next":
                await self._emit_next_finding(session.id)
            elif outcome.action == "propose_close":
                self.storage.update_session(
                    session.id, stage=Stage.AWAITING_CLOSE_CONFIRMATION,
                )
                await self._safe_dm(
                    user.open_id,
                    outcome.dm_text or
                    "BLOCKER 都闭合了 ✅\n回 `a` close 出 summary，或 `more` 看 deferred。",
                )
            elif outcome.action == "force_close":
                await self._safe_dm(user.open_id, "已强制 close，正在出 summary…")
                await self._enqueue_close_chain(session.id, forced=True)
            return

        if session.stage == Stage.AWAITING_CLOSE_CONFIRMATION:
            await self._handle_close_confirmation(user, session, msg)
            return

        BUSY_STAGES = {
            Stage.SCANNING: ("scan_four_pillar", "_do_scan",
                             "我还在挑刺中，再等 30-60 秒"),
            Stage.MERGING: ("merge_draft", "_do_merge",
                            "稿件整合中，再等 20-40 秒"),
            Stage.FINAL_GATING: ("final_gate", "_do_final_gate_default",
                                  "终审中，再等 20-40 秒"),
            Stage.CLOSING: ("build_summary", "_do_build_and_deliver",
                            "summary 生成中，再等 30-60 秒"),
        }
        if session.stage in BUSY_STAGES:
            llm_stage_key, restart_method, busy_msg = BUSY_STAGES[session.stage]
            if not self.storage.has_llm_call_for_stage(session.id, llm_stage_key):
                await self._safe_dm(
                    user.open_id,
                    f"上次我处理 {session.stage.value} 时被打断了，重新跑（约 30-60 秒）",
                )
                fn = getattr(self, restart_method)
                await fn(session.id)
            else:
                await self._safe_dm(user.open_id, busy_msg)
            return

        intent, _ = parse_reply_intent(msg.content_text, stage="qa_loop")
        if intent in (Intent.ACCEPT, Intent.DONE):
            await self._enqueue_close_chain(session.id, forced=False)
        else:
            await self._safe_dm(user.open_id, f"当前 session 在 {session.stage.value}，等我处理完就接着聊。")

    async def _do_final_gate_default(self, session_id: str) -> None:
        """Wrapper for BUSY_STAGES (final_gate needs forced kwarg)."""
        await self._do_final_gate(session_id, forced=False)

    async def _handle_close_confirmation(
        self, user: User, session: Session, msg: IncomingMessage
    ) -> None:
        """Issue #4: AWAITING_CLOSE_CONFIRMATION stage handler. Interprets the
        Requester's reply to the 'BLOCKER 已闭合 ✅ close 还是 more?' prompt."""
        intent, _remainder = parse_reply_intent(msg.content_text, stage="qa_loop")
        self.storage.log_conversation(
            session, role="requester", text=msg.content_text, intent=intent.value,
        )
        if intent in (Intent.ACCEPT, Intent.DONE, Intent.PICK_A):
            self.storage.update_session(session.id, stage=Stage.MERGING)
            await self._safe_dm(user.open_id, "好，正在出 summary，约 30-60 秒…")
            await self._enqueue_close_chain(session.id, forced=False)
            return
        if intent in (Intent.MORE, Intent.PICK_B):
            cursor = self.storage.load_cursor(session)
            moved = cursor.pull_deferred(self.cfg.review.top_n_findings)
            if moved == 0:
                await self._safe_dm(user.open_id, "deferred 也空了。回 `a` close 出 summary。")
                return
            cursor.advance()
            self.storage.save_cursor(session, cursor)
            self.storage.update_session(session.id, stage=Stage.QA_ACTIVE)
            await self._safe_dm(user.open_id, f"📥 拉了 {moved} 条 deferred 进来，先看第一条：")
            await self._emit_next_finding(session.id)
            return
        if intent == Intent.FORCE_CLOSE:
            await self._safe_dm(user.open_id, "已强制 close，正在出 summary…")
            await self._enqueue_close_chain(session.id, forced=True)
            return
        await self._safe_dm(
            user.open_id,
            "BLOCKER 都闭合了 ✅\n"
            "- `a` (或 `done`) — close 出 summary\n"
            "- `more` — 再看几条 deferred (IMPROVEMENT)\n"
            "- 其他文字 — 我会理解为想补充，请说清要补啥",
        )

    # ── stage helpers (each one idempotent / re-entrant) ─
    async def _do_confirm_topic(self, session_id: str) -> None:
        session = self.storage.get_session(session_id)
        assert session is not None
        responder = self.storage.get_user(session.responder_oid)
        requester = self.storage.get_user(session.requester_oid)
        admin_style, review_rules = self._frozen_configs(session)
        try:
            env = await confirm_topic.propose(
                storage=self.storage, llm=self.llm,
                model=self.cfg.llm.fast_model, session=session,
                requester_user=requester, responder_user=responder,
                admin_style=admin_style, review_rules=review_rules,
                responder_profile=self._frozen_profile(session),
            )
        except LLMTerminalFailure as e:
            await self._fail_session(session, Stage.SUBJECT_CONFIRMATION, "confirm_topic", e)
            return
        await self._safe_dm(session.requester_oid, env.get("im_message",
            "我看到几个候选话题。回 a/b/c 或自己描述。"))

    async def _do_scan(self, session_id: str) -> None:
        session = self.storage.get_session(session_id)
        assert session is not None
        if session.stage not in (Stage.SCANNING, Stage.SUBJECT_CONFIRMATION):
            return
        self.storage.update_session(session.id, stage=Stage.SCANNING)
        responder = self.storage.get_user(session.responder_oid)
        admin_style, review_rules = self._frozen_configs(session)
        try:
            await scan.run(
                storage=self.storage, llm=self.llm,
                model=self.cfg.llm.default_model, session=session,
                responder_user=responder, admin_style=admin_style,
                review_rules=review_rules,
                responder_profile=self._frozen_profile(session),
                top_n=self.cfg.review.top_n_findings,
            )
        except LLMTerminalFailure as e:
            await self._fail_session(session, Stage.SCANNING, "scan", e)
            return
        await self._emit_next_finding(session_id)

    async def _emit_next_finding(self, session_id: str) -> None:
        session = self.storage.get_session(session_id)
        assert session is not None
        responder = self.storage.get_user(session.responder_oid)
        admin_style, review_rules = self._frozen_configs(session)
        try:
            body_text = await qa_loop.emit_current(
                storage=self.storage, llm=self.llm,
                model=self.cfg.llm.default_model, session=session,
                responder_user=responder,
                admin_style=admin_style, review_rules=review_rules,
                responder_profile=self._frozen_profile(session),
                max_rounds=self.cfg.review.max_rounds,
            )
        except LLMTerminalFailure as e:
            await self._fail_session(session, Stage.QA_ACTIVE, "qa_loop", e)
            return
        if not body_text:
            return
        cursor = self.storage.load_cursor(session)
        findings = self.storage.load_findings(session)
        f = next((x for x in findings if x.get("id") == cursor.current_id), None)
        if f is None:
            await self._safe_dm(session.requester_oid, body_text)
            return
        post = _format.build_finding_post(
            finding_id=f.get("id", ""),
            pillar=f.get("pillar", ""),
            severity=f.get("severity", ""),
            source=f.get("source", ""),
            body_text=body_text,
            round_no=session.round_no,
            max_rounds=self.cfg.review.max_rounds,
            remaining=len(cursor.pending),
            deferred=len(cursor.deferred),
        )
        try:
            await self.lark.send_dm_post(session.requester_oid, post)
        except Exception as e:
            _logger.warning("send_dm_post failed (%s); falling back to text", e)
            fallback = _format.build_text_fallback(
                finding_id=f.get("id", ""), pillar=f.get("pillar", ""),
                severity=f.get("severity", ""), source=f.get("source", ""),
                body_text=body_text, round_no=session.round_no,
                max_rounds=self.cfg.review.max_rounds,
                remaining=len(cursor.pending), deferred=len(cursor.deferred),
            )
            await self._safe_dm(session.requester_oid, fallback)

    async def _do_merge(self, session_id: str) -> None:
        session = self.storage.get_session(session_id)
        responder = self.storage.get_user(session.responder_oid)
        admin_style, review_rules = self._frozen_configs(session)
        try:
            await merge_draft.run(
                storage=self.storage, llm=self.llm,
                model=self.cfg.llm.default_model, session=session,
                responder_user=responder, admin_style=admin_style,
                review_rules=review_rules,
                responder_profile=self._frozen_profile(session),
            )
        except LLMTerminalFailure as e:
            await self._fail_session(session, Stage.MERGING, "merge_draft", e)

    async def _do_final_gate(self, session_id: str, *, forced: bool) -> None:
        session = self.storage.get_session(session_id)
        responder = self.storage.get_user(session.responder_oid)
        admin_style, review_rules = self._frozen_configs(session)
        try:
            await final_gate.run(
                storage=self.storage, llm=self.llm,
                model=self.cfg.llm.default_model, session=session,
                responder_user=responder, admin_style=admin_style,
                review_rules=review_rules,
                responder_profile=self._frozen_profile(session),
                forced=forced,
            )
        except LLMTerminalFailure as e:
            await self._fail_session(session, Stage.FINAL_GATING, "final_gate", e)

    async def _do_build_and_deliver(self, session_id: str) -> None:
        session = self.storage.get_session(session_id)
        responder = self.storage.get_user(session.responder_oid)
        requester = self.storage.get_user(session.requester_oid)
        admin_style, review_rules = self._frozen_configs(session)
        try:
            await build_summary.run(
                storage=self.storage, llm=self.llm,
                model=self.cfg.llm.default_model, session=session,
                requester_user=requester, responder_user=responder,
                admin_style=admin_style, review_rules=review_rules,
                responder_profile=self._frozen_profile(session),
            )
        except LLMTerminalFailure as e:
            await self._fail_session(session, Stage.CLOSING, "build_summary", e)
            return
        targets = deliver.load_targets(
            self.storage, fs_root=self.cfg.paths.fs,
            requester_oid=session.requester_oid, responder_oid=session.responder_oid,
        )
        await deliver.run(
            storage=self.storage, session=self.storage.get_session(session_id),
            backends=self.delivery_backends, targets=targets,
        )

    # ── close chain (sequential enqueues) ────────────────
    async def _enqueue_close_chain(self, session_id: str, *, forced: bool) -> None:
        """Round-final B1: close chain must honour final_gate verdict.
        If gate FAILs and fail_count < max, reopen Q&A instead of delivering.
        If fail_count >= max, force PARTIAL and continue delivery."""
        from ..core.enums import Verdict
        from ..pipeline.qa_loop import transition_after_final_gate_fail

        await self._do_merge(session_id)
        await self._do_final_gate(session_id, forced=forced)
        s = self.storage.get_session(session_id)
        assert s is not None
        if s.verdict == Verdict.FAIL and not forced:
            if s.fail_count < self.cfg.review.final_gate_max_fail_count:
                regressions = self._extract_open_blockers(s)
                transition_after_final_gate_fail(
                    storage=self.storage, session=s,
                    regression_finding_ids=regressions,
                )
                await self._safe_dm(
                    s.requester_oid,
                    f"终审发现 {len(regressions)} 条新 BLOCKER/REGRESSION，重启 Q&A 补充。",
                )
                await self._emit_next_finding(session_id)
                return
            else:
                _logger.warning(
                    "final gate failed %d times for %s — forcing PARTIAL",
                    s.fail_count, session_id,
                )
        self.storage.update_session(session_id, stage=Stage.CLOSING)
        await self._do_build_and_deliver(session_id)

    def _extract_open_blockers(self, session: Session) -> list[str]:
        findings = self.storage.load_findings(session)
        return [f.get("id", "") for f in findings
                if f.get("status") == "open"
                and f.get("severity") in ("BLOCKER", "REGRESSION")]

    # ── config helpers ──────────────────────────────────
    def _global_configs(self) -> tuple[str, str]:
        admin_style_path = Path(self.cfg.paths.fs) / "config" / "admin_style.md"
        review_rules_path = Path(self.cfg.paths.fs) / "config" / "review_rules.md"
        admin_style = admin_style_path.read_text() if admin_style_path.exists() else ""
        review_rules = review_rules_path.read_text() if review_rules_path.exists() else ""
        return admin_style, review_rules

    def _frozen_configs(self, session: Session) -> tuple[str, str]:
        return (session.admin_style or "", session.review_rules or "")

    def _responder_profile_for(self, responder_oid: str) -> str:
        path = Path(self.cfg.paths.fs) / "config" / f"responder_{responder_oid}.md"
        return path.read_text() if path.exists() else ""

    def _frozen_profile(self, session: Session) -> str:
        return session.responder_profile or ""

    # ── utilities ───────────────────────────────────────

    async def _safe_dm(self, open_id: str, text: str) -> None:
        try:
            await self.lark.send_dm_text(open_id, text)
        except Exception as e:
            _logger.error("_safe_dm(%s, …) failed: %s", open_id, e)

    async def _fail_session(
        self, session: Session, stage: Stage, label: str, exc: Exception,
    ) -> None:
        _logger.error("session %s failed at %s (%s): %s", session.id, stage, label, exc)
        self.storage.update_session(
            session.id,
            stage=stage,
            status=SessionStatus.FAILED,
        )
        message = "系统错误，session 失败了。稍后再试。"
        if isinstance(exc, LLMTerminalFailure):
            message = f"LLM 调用失败 ({label})，session 已终止。联系 admin。"
        await self._safe_dm(session.requester_oid, message)


def _extract_urls_simple(text: str) -> list[str]:
    """Extract http/https URLs from text. Returns deduplicated list."""
    import re
    urls = re.findall(r"https?://[^\s<>\"')\]]+", text)
    cleaned = [u.rstrip(".,;:!?）)") for u in urls]
    seen = set()
    result = []
    for u in cleaned:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result
