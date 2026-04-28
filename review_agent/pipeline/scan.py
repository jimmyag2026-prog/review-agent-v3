from __future__ import annotations

from pathlib import Path

from ..core.enums import FindingSource, Pillar, Severity, Stage
from ..core.models import Anchor, Cursor, Finding, Session, User
from ..core.storage import Storage
from ..llm.base import LLMClient
from ..util.ids import now_iso
from ..util.md import line_range_snippet, text_hash
from ._json import extract
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
    top_n: int,
) -> Cursor:
    fs = Path(session.fs_path)
    normalized = (fs / "normalized.md").read_text(encoding="utf-8")

    persona_kwargs = dict(
        responder_name=responder_user.display_name,
        admin_style=admin_style, review_rules=review_rules,
        responder_profile=responder_profile,
    )
    system = render("persona.md.j2", **persona_kwargs)

    user_a = render(
        "scan_four_pillar.md.j2",
        subject=session.subject or "(untitled)", round=session.round_no,
        normalized=normalized, **persona_kwargs,
    )
    # Issue #6 fix: bump max_tokens. DeepSeek reasoning models can spend 3-4k
    # tokens just thinking before emitting JSON; if max_tokens is too tight
    # the reasoning eats all of it and content comes back empty (finish=length).
    resp_a = await llm.chat(system=system, user=user_a, model=model, max_tokens=8192)
    storage.log_llm_call(
        session_id=session.id, stage="scan_four_pillar", model=resp_a.model,
        prompt_tokens=resp_a.prompt_tokens, completion_tokens=resp_a.completion_tokens,
        reasoning_tokens=resp_a.reasoning_tokens, cache_hit_tokens=resp_a.cache_hit_tokens,
        latency_ms=resp_a.latency_ms, finish_reason=resp_a.finish_reason, ok=True, error=None,
    )
    findings_a = _parse_findings(resp_a.content, normalized, FindingSource.FOUR_PILLAR, "p")

    user_b = render(
        "scan_responder_sim.md.j2",
        subject=session.subject or "(untitled)", normalized=normalized, **persona_kwargs,
    )
    resp_b = await llm.chat(system=system, user=user_b, model=model, max_tokens=4096)
    storage.log_llm_call(
        session_id=session.id, stage="scan_responder_sim", model=resp_b.model,
        prompt_tokens=resp_b.prompt_tokens, completion_tokens=resp_b.completion_tokens,
        reasoning_tokens=resp_b.reasoning_tokens, cache_hit_tokens=resp_b.cache_hit_tokens,
        latency_ms=resp_b.latency_ms, finish_reason=resp_b.finish_reason, ok=True, error=None,
    )
    findings_b = _parse_findings(resp_b.content, normalized, FindingSource.RESPONDER_SIM, "r")

    all_findings = findings_a + findings_b
    for f in all_findings:
        storage.append_finding(session, f)

    sorted_ids = _prioritize(all_findings)
    cursor = Cursor()
    cursor.pending = sorted_ids[:top_n]
    cursor.deferred = sorted_ids[top_n:]
    if cursor.pending:
        cursor.current_id = cursor.pending.pop(0)
    storage.save_cursor(session, cursor)
    storage.update_session(session.id, stage=Stage.QA_ACTIVE)
    return cursor


def _parse_findings(
    content: str, normalized: str, source: FindingSource, prefix: str
) -> list[Finding]:
    env = extract(content)
    raw_findings = env.get("findings", []) if isinstance(env, dict) else []
    out: list[Finding] = []
    for i, item in enumerate(raw_findings, start=1):
        try:
            pillar = Pillar(item["pillar"])
            severity = Severity(item["severity"].upper().replace("-TO-HAVE", "-TO-HAVE"))
            anchor_raw = item.get("anchor", {}) or {}
            line_range = anchor_raw.get("line_range")
            snippet = anchor_raw.get("snippet", "")
            if not snippet and line_range:
                snippet = line_range_snippet(normalized, line_range[0], line_range[-1])
            anchor = Anchor(
                source="normalized.md",
                section=anchor_raw.get("section"),
                line_range=tuple(line_range) if line_range else None,
                text_hash=text_hash(snippet) if snippet else None,
                snippet=snippet,
            )
            fid = item.get("id") or f"{prefix}{i}"
            out.append(Finding(
                id=fid, round=1, created_at=now_iso(),
                source=source, pillar=pillar, severity=severity,
                issue=item.get("issue", ""), suggest=item.get("suggest", ""),
                anchor=anchor,
                simulated_question=item.get("simulated_question"),
                priority=item.get("priority"),
            ))
        except (KeyError, ValueError):
            continue
    return out


_SEV_ORDER = {Severity.BLOCKER: 0, Severity.IMPROVEMENT: 1, Severity.NICE_TO_HAVE: 2}


def _prioritize(findings: list[Finding]) -> list[str]:
    def key(f: Finding) -> tuple:
        intent_first = 0 if f.pillar == Pillar.INTENT else 1
        return (_SEV_ORDER.get(f.severity, 9), intent_first, f.priority or 99, f.id)
    return [f.id for f in sorted(findings, key=key)]
