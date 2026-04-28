from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..core.enums import FindingStatus, Intent, Stage
from ..core.models import Cursor, Session, User
from ..core.storage import Storage
from ..llm.base import LLMClient
from ..util.ids import now_iso
from ._intents import parse_reply_intent
from ._prompts import render


@dataclass
class TurnOutcome:
    action: Literal["emit_next", "propose_close", "force_close", "no_op"]
    dm_text: str = ""
    advanced: bool = False


async def emit_current(
    *,
    storage: Storage,
    llm: LLMClient,
    model: str,
    session: Session,
    responder_user: User,
    admin_style: str,
    review_rules: str,
    responder_profile: str,
    max_rounds: int,
) -> str:
    cursor = storage.load_cursor(session)
    if cursor.current_id is None:
        return ""
    findings = storage.load_findings(session)
    finding = next((f for f in findings if f["id"] == cursor.current_id), None)
    if not finding:
        return ""
    persona_kwargs = dict(
        responder_name=responder_user.display_name,
        admin_style=admin_style, review_rules=review_rules,
        responder_profile=responder_profile,
    )
    system = render("persona.md.j2", **persona_kwargs)
    user = render(
        "qa_emit_finding.md.j2",
        finding=finding, round=session.round_no, max_rounds=max_rounds,
        remaining=len(cursor.pending), deferred=len(cursor.deferred),
        **persona_kwargs,
    )
    # Issue #6 fix: bump max_tokens — short body but reasoning model still
    # spends ~500-800 tokens thinking before emitting the 问题:/建议: lines.
    resp = await llm.chat(system=system, user=user, model=model, max_tokens=2048)
    storage.log_llm_call(
        session_id=session.id, stage="qa_emit_finding", model=resp.model,
        prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
        reasoning_tokens=resp.reasoning_tokens, cache_hit_tokens=resp.cache_hit_tokens,
        latency_ms=resp.latency_ms, finish_reason=resp.finish_reason, ok=True, error=None,
    )
    return resp.content.strip()


def handle_reply(
    *, storage: Storage, session: Session, reply: str, top_n_more: int = 5
) -> TurnOutcome:
    cursor = storage.load_cursor(session)
    intent, remainder = parse_reply_intent(reply, stage="qa_loop")
    storage.log_conversation(session, role="requester", text=reply, intent=intent.value)

    if intent == Intent.MORE:
        moved = cursor.pull_deferred(top_n_more)
        storage.save_cursor(session, cursor)
        if moved == 0:
            return TurnOutcome("propose_close", dm_text="没有 deferred 了。要 close 吗？(a) close (b) 等会")
        if cursor.current_id is None:
            cursor.advance()
            storage.save_cursor(session, cursor)
        return TurnOutcome("emit_next", advanced=True)

    if intent == Intent.DONE:
        return TurnOutcome("propose_close", dm_text="确认 close？(a) close (b) 再过一条")

    if intent == Intent.FORCE_CLOSE:
        return TurnOutcome("force_close")

    cur_id = cursor.current_id
    if cur_id is None:
        return TurnOutcome("no_op")

    findings = storage.load_findings(session)
    cur_finding = next((f for f in findings if f["id"] == cur_id), None)

    if intent == Intent.ACCEPT:
        storage.update_finding_status(
            session, cur_id, status=FindingStatus.ACCEPTED.value,
            reply=remainder, replied_at=now_iso(),
        )
    elif intent == Intent.REJECT:
        storage.update_finding_status(
            session, cur_id, status=FindingStatus.REJECTED.value,
            reply=remainder, replied_at=now_iso(),
        )
        if cur_finding:
            storage.append_dissent(session, cur_finding, remainder)
    elif intent == Intent.MODIFY:
        storage.update_finding_status(
            session, cur_id, status=FindingStatus.MODIFIED.value,
            reply=remainder, replied_at=now_iso(),
        )
    elif intent == Intent.PASS:
        # do not change status; just advance
        pass
    elif intent == Intent.QUESTION:
        return TurnOutcome("no_op")  # caller handles clarification

    cursor.advance()
    storage.save_cursor(session, cursor)

    if cursor.current_id is None and not cursor.pending:
        return TurnOutcome("propose_close",
                           dm_text=f"BLOCKER 已闭合 ✅ 还有 {len(cursor.deferred)} 条 deferred。"
                                   "(a) close (b) more (custom) 我有补充")

    return TurnOutcome("emit_next", advanced=True)


def transition_after_final_gate_fail(
    *, storage: Storage, session: Session, regression_finding_ids: list[str]
) -> Cursor:
    """Round-1 B3 / Round-2 NB1: final-gate FAIL → push regressions to pending head."""
    cursor = storage.load_cursor(session)
    cursor.regression_rescan = True
    cursor.pending = regression_finding_ids + cursor.pending
    if cursor.current_id is None:
        cursor.advance()
    storage.save_cursor(session, cursor)
    storage.update_session(session.id, stage=Stage.QA_ACTIVE_REOPENED,
                           round_no=session.round_no + 1)
    return cursor
