from __future__ import annotations

from pathlib import Path

from ..core.enums import FindingStatus, Stage
from ..core.models import Session, User
from ..core.storage import Storage
from ..llm.base import LLMClient
from ..util.path import atomic_write
from ._prompts import render


async def run(
    *,
    storage: Storage,
    llm: LLMClient,
    model: str,
    session: Session,
    responder_user: User,
    admin_style: str,
    review_rules: str,
    responder_profile: str,
) -> Path:
    fs = Path(session.fs_path)
    normalized = (fs / "normalized.md").read_text(encoding="utf-8")
    findings = storage.load_findings(session)
    accepted = [f for f in findings if f.get("status") == FindingStatus.ACCEPTED.value]

    persona_kwargs = dict(
        responder_name=responder_user.display_name,
        admin_style=admin_style, review_rules=review_rules,
        responder_profile=responder_profile,
    )
    system = render("persona.md.j2", **persona_kwargs)
    user = render(
        "merge_draft.md.j2",
        normalized=normalized, accepted=accepted, **persona_kwargs,
    )
    resp = await llm.chat(system=system, user=user, model=model, max_tokens=8192)
    storage.log_llm_call(
        session_id=session.id, stage="merge_draft", model=resp.model,
        prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
        reasoning_tokens=resp.reasoning_tokens, cache_hit_tokens=resp.cache_hit_tokens,
        latency_ms=resp.latency_ms, finish_reason=resp.finish_reason, ok=True, error=None,
    )
    out = fs / "final" / "revised.md"
    atomic_write(out, resp.content)
    storage.update_session(session.id, stage=Stage.FINAL_GATING)
    return out
