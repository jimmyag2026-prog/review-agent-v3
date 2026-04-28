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
        if sender is None:
            await self._safe_dm(msg.sender_open_id, render("dm_templates.md.j2") + "")
            await self._safe_dm(
                msg.sender_open_id,
                "Hi, I'm review-agent. Ask my admin to add you.",
            )
            return

        active = self.storage.get_active_session_for(sender.open_id)

        if Role.REQUESTER in sender.roles:
            if active:
                await self._handle_requester_in_session(sender, active, msg)
            else:
                await self._handle_requester_no_session(sender, msg)
            return

        # Admin / Responder: v0 just acknowledges; full admin chat is via CLI
        await self._safe_dm(
            sender.open_id,
            f"Hi {sender.display_name}, admin/responder controls live in the CLI / dashboard."
            " I'll DM you summaries when sessions close.",
        )

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
        # save raw text input (file handling is separate; v0 covers text + saved-as-file)
        if msg.msg_type == "text" and msg.content_text.strip():
            text_path = resolve_session_path(
                self.cfg.paths.fs, user.open_id, session.id,
                f"input/{now_iso().replace(':', '')}_text.md",
            )
            atomic_write(text_path, msg.content_text)
            try:
                result = await self.ingest.run(session, text_path.name)
            except Exception as e:
                await self._fail_session(session, Stage.INGEST_FAILED, "ingest", e)
                return
            self.storage.update_session(session.id, stage=Stage.SUBJECT_CONFIRMATION)
            await self._do_confirm_topic(session.id)
        else:
            await self._safe_dm(user.open_id,
                                "收到。文件类型 v0 还不支持，能直接贴正文给我吗？")

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
                await self._safe_dm(user.open_id, outcome.dm_text or "可以 close 了？回 a 确认。")
            elif outcome.action == "force_close":
                await self._safe_dm(user.open_id, "已强制 close，正在出 summary…")
                await self._enqueue_close_chain(session.id, forced=True)
            return

        intent, _ = parse_reply_intent(msg.content_text, stage="qa_loop")
        if intent in (Intent.ACCEPT, Intent.DONE):
            await self._enqueue_close_chain(session.id, forced=False)
        else:
            await self._safe_dm(user.open_id, f"当前 session 在 {session.stage.value}，等我处理完就接着聊。")

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
            return  # idempotent
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
            text = await qa_loop.emit_current(
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
        if text:
            await self._safe_dm(session.requester_oid, text)

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
                    f"final-gate 发现 {len(regressions)} 处回归，再过一轮。第一条："
                )
                await self._emit_next_finding(session_id)
                return
            # fail_count over cap → force partial + still deliver
            self.storage.update_session(session_id, verdict=Verdict.FORCED_PARTIAL,
                                         stage=Stage.CLOSING)
        await self._do_build_and_deliver(session_id)

    def _extract_open_blockers(self, session: Session) -> list[str]:
        from ..core.enums import FindingStatus, Severity
        findings = self.storage.load_findings(session)
        return [
            f["id"] for f in findings
            if f.get("severity") == Severity.BLOCKER.value
            and f.get("status") in (FindingStatus.OPEN.value, None)
        ]

    # ── failure helper ──────────────────────────────────
    async def _fail_session(self, session: Session, stage: Stage, stage_name: str, err: Exception) -> None:
        self.storage.update_session(
            session.id, stage=Stage.FAILED, status=SessionStatus.FAILED,
            failed_stage=stage, last_error=str(err)[:500],
        )
        msg = render("dm_templates.md.j2") + ""
        await self._safe_dm(
            session.requester_oid,
            self._failure_text(stage_name),
        )

    def _failure_text(self, stage_name: str) -> str:
        table = {
            "ingest": "材料处理卡住了。Admin 已收通知，可以换种格式重发（直接贴正文最稳）。",
            "confirm_topic": "我在确认主题时卡了。再发一遍材料试试，或等 admin 处理。",
            "scan": "扫描材料卡住了。这次的 review 暂停，admin 已收通知。",
            "qa_loop": "我突然卡了。最近的回复我会保留，等会再发一次试试。",
            "merge_draft": "整合稿件失败。已存的 dissent + accepted findings 都还在。",
            "final_gate": "final gate 失败。dissent + 最终材料都已存，admin 会人工处理。",
            "build_summary": "Summary 生成失败。admin 已收通知。",
        }
        return table.get(stage_name, "卡了一下，admin 已收通知。")

    # ── lookup helpers ──────────────────────────────────
    def _global_configs(self) -> tuple[str, str]:
        fs = Path(self.cfg.paths.fs)
        admin_style = (fs / "admin_style.md").read_text() if (fs / "admin_style.md").exists() else _DEFAULT_ADMIN_STYLE
        review_rules = (fs / "rules" / "review_rules.md").read_text() if (fs / "rules" / "review_rules.md").exists() else _DEFAULT_REVIEW_RULES
        return admin_style, review_rules

    def _responder_profile_for(self, responder_oid: str) -> str:
        # round-final B2: defense-in-depth, reject path-escape characters in oid
        if "/" in responder_oid or ".." in responder_oid or responder_oid.startswith("."):
            raise ValueError(f"invalid responder_oid: {responder_oid!r}")
        path = Path(self.cfg.paths.fs) / "users" / responder_oid / "profile.md"
        if path.exists():
            return path.read_text()
        return _DEFAULT_RESPONDER_PROFILE

    def _frozen_configs(self, session: Session) -> tuple[str, str]:
        fs = Path(session.fs_path)
        return (
            (fs / "admin_style.md").read_text(),
            (fs / "review_rules.md").read_text(),
        )

    def _frozen_profile(self, session: Session) -> str:
        return (Path(session.fs_path) / "profile.md").read_text()

    async def _safe_dm(self, open_id: str, text: str) -> None:
        try:
            await self.lark.send_dm_text(open_id, text)
        except Exception as e:  # never let outbound failure crash the worker
            _logger.warning("send_dm_text failed to %s: %s", open_id, e)


_DEFAULT_ADMIN_STYLE = """tone: direct, no corporate fluff
language_mirroring: true
response_length_cap_chars: 300
message_pacing: one_finding_per_message
emoji_policy: minimal
document_editing: suggest
"""

_DEFAULT_REVIEW_RULES = """- 4 pillars: Background / Materials / Framework / Intent
- Intent is CSW gate (always BLOCKER if vague)
- Max 3 rounds (5 with explicit Requester request)
- Top 5 findings emitted; rest deferred until 'more'
- Dissent always recorded; never silently dropped
"""

_DEFAULT_RESPONDER_PROFILE = """# Responder Profile (default)
我是一个挑剔的高管。我看材料时关注：
- ROI 是否清晰：收益和成本必须对得上
- 数据来源：每个数字必须有出处和日期
- 反向案例：方案的最大反方观点是什么
- Plan B：如果主推方案失败，备选是什么
- Stakeholder 真实声音，不是想象

我讨厌的：含糊 ask、把决策推回我、空话（"我们要重视 X"）。
"""
