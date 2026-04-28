from __future__ import annotations

from pathlib import Path

from ..core.enums import Intent, Stage
from ..core.models import Session, User
from ..core.storage import Storage
from ..llm.base import LLMClient
from ._intents import parse_reply_intent
from ._json import extract
from ._prompts import render


async def propose(
    *,
    storage: Storage,
    llm: LLMClient,
    model: str,
    session: Session,
    requester_user: User,
    responder_user: User,
    admin_style: str,
    review_rules: str,
    responder_profile: str,
) -> dict:
    """Run LLM to propose 2-4 candidate subjects. Returns the parsed envelope."""
    fs = Path(session.fs_path)
    normalized = (fs / "normalized.md").read_text(encoding="utf-8")
    system = render(
        "persona.md.j2",
        responder_name=responder_user.display_name,
        admin_style=admin_style, review_rules=review_rules,
        responder_profile=responder_profile,
    )
    user = render(
        "confirm_topic.md.j2",
        responder_name=responder_user.display_name,
        admin_style=admin_style, review_rules=review_rules,
        responder_profile=responder_profile,
        normalized=normalized, recent_messages="",
    )
    resp = await llm.chat(system=system, user=user, model=model, max_tokens=2048)
    env = extract(resp.content)
    storage.log_llm_call(
        session_id=session.id, stage="confirm_topic", model=resp.model,
        prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
        reasoning_tokens=resp.reasoning_tokens, cache_hit_tokens=resp.cache_hit_tokens,
        latency_ms=resp.latency_ms, finish_reason=resp.finish_reason, ok=True, error=None,
    )
    storage.update_session(session.id, stage=Stage.SUBJECT_CONFIRMATION,
                           meta={**session.meta, "topic_candidates": env.get("candidates", [])})
    return env


def handle_reply(
    *, storage: Storage, session: Session, reply: str
) -> tuple[Intent, str | None]:
    """Resolve a reply during subject_confirmation. Returns (intent, chosen_subject_or_None)."""
    intent, remainder = parse_reply_intent(reply, stage="subject_confirmation")
    candidates = (session.meta or {}).get("topic_candidates", [])
    chosen: str | None = None
    if intent == Intent.PICK_A and candidates:
        chosen = candidates[0]["topic"]
    elif intent == Intent.PICK_B and len(candidates) > 1:
        chosen = candidates[1]["topic"]
    elif intent == Intent.PICK_C and len(candidates) > 2:
        chosen = candidates[2]["topic"]
    elif intent == Intent.CUSTOM and remainder:
        chosen = remainder
    if chosen:
        storage.update_session(session.id, subject=chosen, stage=Stage.SCANNING)
        storage.log_conversation(session, role="requester", text=reply, intent=intent.value)
    return intent, chosen
