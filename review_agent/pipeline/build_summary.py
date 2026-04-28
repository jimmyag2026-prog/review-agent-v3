from __future__ import annotations

from pathlib import Path

from ..core.enums import FindingStatus
from ..core.models import Session, User
from ..core.storage import Storage
from ..llm.base import LLMClient
from ..util.ids import now_iso
from ..util.path import atomic_write
from ._prompts import render


async def run(
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
) -> Path:
    fs = Path(session.fs_path)
    revised_path = fs / "final" / "revised.md"
    if not revised_path.exists():
        revised_path = fs / "normalized.md"
    revised = revised_path.read_text(encoding="utf-8")
    dissent = (fs / "dissent.md").read_text(encoding="utf-8") if (fs / "dissent.md").exists() else ""

    findings = storage.load_findings(session)
    accepted = [f for f in findings if f.get("status") == FindingStatus.ACCEPTED.value]
    unresolvable = [f for f in findings if f.get("status") == FindingStatus.UNRESOLVABLE.value]

    persona_kwargs = dict(
        responder_name=responder_user.display_name,
        admin_style=admin_style, review_rules=review_rules,
        responder_profile=responder_profile,
    )
    system = render("persona.md.j2", **persona_kwargs)
    user = render(
        "build_summary.md.j2",
        subject=session.subject or "(untitled)", rounds=session.round_no,
        ts=now_iso(), requester_display=requester_user.display_name,
        revised=revised, accepted=accepted, dissent=dissent,
        unresolvable=unresolvable,
        **persona_kwargs,
    )
    resp = await llm.chat(system=system, user=user, model=model, max_tokens=6144)
    storage.log_llm_call(
        session_id=session.id, stage="build_summary", model=resp.model,
        prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
        reasoning_tokens=resp.reasoning_tokens, cache_hit_tokens=resp.cache_hit_tokens,
        latency_ms=resp.latency_ms, finish_reason=resp.finish_reason, ok=True, error=None,
    )
    out = fs / "summary.md"
    atomic_write(out, resp.content)

    audit_path = fs / "summary_audit.md"
    audit_lines = [
        f"# Audit — {session.subject or '(untitled)'}",
        f"Session: {session.id} · Rounds: {session.round_no} · Verdict: {session.verdict}",
        "\n## Findings counts",
    ]
    by_pillar: dict[str, dict[str, int]] = {}
    for f in findings:
        pillar = f.get("pillar", "Unknown")
        sev = f.get("severity", "?")
        by_pillar.setdefault(pillar, {})[sev] = by_pillar.setdefault(pillar, {}).get(sev, 0) + 1
    for pillar, sevs in by_pillar.items():
        audit_lines.append(f"- {pillar}: " + ", ".join(f"{k}={v}" for k, v in sevs.items()))
    audit_lines.append(f"\n## Accepted ({len(accepted)})")
    for f in accepted:
        audit_lines.append(f"- [{f.get('pillar')}/{f.get('severity')}] {f.get('issue')}")
    audit_lines.append(f"\n## Unresolvable ({len(unresolvable)})")
    for f in unresolvable:
        audit_lines.append(
            f"- [{f.get('pillar')}] {f.get('issue')} — {f.get('unresolvable_reason')}"
        )
    audit_lines.append("\n## Dissent\n")
    audit_lines.append(dissent)
    atomic_write(audit_path, "\n".join(audit_lines))
    return out
