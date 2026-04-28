from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from ..core.enums import FindingStatus, Pillar, Severity, Stage, Verdict
from ..core.models import GateOutcome, Session, User
from ..core.storage import Storage
from ..llm.base import LLMClient
from ..util.path import atomic_write
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
    forced: bool = False,
) -> GateOutcome:
    fs = Path(session.fs_path)
    revised_path = fs / "final" / "revised.md"
    if not revised_path.exists():
        # if no revised, gate against normalized (forced close path)
        revised_path = fs / "normalized.md"
    revised = revised_path.read_text(encoding="utf-8")

    persona_kwargs = dict(
        responder_name=responder_user.display_name,
        admin_style=admin_style, review_rules=review_rules,
        responder_profile=responder_profile,
    )
    system = render("persona.md.j2", **persona_kwargs)
    user = render("final_gate.md.j2", revised=revised, **persona_kwargs)
    resp = await llm.chat(system=system, user=user, model=model, max_tokens=4096)
    storage.log_llm_call(
        session_id=session.id, stage="final_gate", model=resp.model,
        prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
        reasoning_tokens=resp.reasoning_tokens, cache_hit_tokens=resp.cache_hit_tokens,
        latency_ms=resp.latency_ms, finish_reason=resp.finish_reason, ok=True, error=None,
    )
    env = extract(resp.content)

    findings = storage.load_findings(session)
    pillar_counts = _aggregate_counts(findings)
    by_source = _aggregate_sources(findings)

    pillar_verdict = env.get("pillar_verdict", {})
    csw = env.get("csw_gate_status", "pass")
    raw_verdict = env.get("verdict", "READY").upper()

    if forced:
        verdict = Verdict.FORCED_PARTIAL
    elif csw == "fail" or pillar_verdict.get(Pillar.INTENT.value) == "fail":
        verdict = Verdict.FAIL
    elif raw_verdict == "READY_WITH_OPEN_ITEMS":
        verdict = Verdict.READY_WITH_OPEN_ITEMS
    elif raw_verdict == "FAIL":
        verdict = Verdict.FAIL
    else:
        verdict = Verdict.READY

    outcome = GateOutcome(
        verdict=verdict, csw_gate_status=csw,
        pillar_verdict=pillar_verdict,
        pillar_counts=pillar_counts, by_source=by_source,
        regressions=env.get("regressions", []),
    )
    atomic_write(fs / "verdict.json", json.dumps(outcome.to_dict(), indent=2, ensure_ascii=False))

    if verdict == Verdict.FAIL:
        storage.update_session(
            session.id, fail_count=session.fail_count + 1,
            verdict=verdict,
        )
    else:
        storage.update_session(
            session.id, verdict=verdict, stage=Stage.CLOSING,
        )
    return outcome


def _aggregate_counts(findings: list[dict]) -> dict[str, dict[str, int]]:
    counts: dict = defaultdict(lambda: {"pass": 0, "open_blocker": 0, "unresolvable": 0, "total": 0})
    for f in findings:
        pillar = f.get("pillar", "Unknown")
        counts[pillar]["total"] += 1
        sev = f.get("severity")
        status = f.get("status", FindingStatus.OPEN.value)
        if status in {FindingStatus.ACCEPTED.value, FindingStatus.MODIFIED.value}:
            counts[pillar]["pass"] += 1
        elif status == FindingStatus.UNRESOLVABLE.value:
            counts[pillar]["unresolvable"] += 1
        elif sev == Severity.BLOCKER.value:
            counts[pillar]["open_blocker"] += 1
    return dict(counts)


def _aggregate_sources(findings: list[dict]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for f in findings:
        out[f.get("source", "unknown")] += 1
    return dict(out)
