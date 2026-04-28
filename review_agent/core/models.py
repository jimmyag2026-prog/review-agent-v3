from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .enums import (
    FindingSource,
    FindingStatus,
    Pillar,
    Role,
    SessionStatus,
    Severity,
    Stage,
    Verdict,
)


@dataclass
class User:
    open_id: str
    display_name: str
    roles: list[Role]
    pairing_responder_oid: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def has_role(self, role: Role) -> bool:
        return role in self.roles


@dataclass
class Session:
    id: str
    requester_oid: str
    responder_oid: str
    fs_path: str
    started_at: str
    stage: Stage = Stage.INTAKE
    status: SessionStatus = SessionStatus.ACTIVE
    round_no: int = 1
    subject: str | None = None
    closed_at: str | None = None
    verdict: Verdict | None = None
    trigger_source: str = "dm"
    failed_stage: Stage | None = None
    last_error: str | None = None
    fail_count: int = 0
    meta: dict[str, Any] = field(default_factory=dict)
    # frozen-at-session-start config snapshots (loaded from fs files by storage)
    admin_style: str = ""
    review_rules: str = ""
    responder_profile: str = ""


@dataclass
class Anchor:
    source: str = "normalized.md"
    section: str | None = None
    line_range: tuple[int, int] | None = None
    text_hash: str | None = None
    snippet: str = ""

    def to_dict(self) -> dict:
        d: dict = {"source": self.source, "snippet": self.snippet}
        if self.section is not None:
            d["section"] = self.section
        if self.line_range is not None:
            d["line_range"] = list(self.line_range)
        if self.text_hash is not None:
            d["text_hash"] = self.text_hash
        return d


@dataclass
class Finding:
    id: str
    round: int
    created_at: str
    source: FindingSource
    pillar: Pillar
    severity: Severity
    issue: str
    suggest: str
    anchor: Anchor = field(default_factory=Anchor)
    status: FindingStatus = FindingStatus.OPEN
    simulated_question: str | None = None
    priority: int | None = None
    reply: str | None = None
    replied_at: str | None = None
    unresolvable_reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    escalated_to_open_items: bool = False

    def to_jsonl(self) -> dict:
        d = asdict(self)
        d["anchor"] = self.anchor.to_dict()
        d["source"] = self.source.value
        d["pillar"] = self.pillar.value
        d["severity"] = self.severity.value
        d["status"] = self.status.value
        return {k: v for k, v in d.items() if v is not None and v != "" and v != {}}


@dataclass
class Cursor:
    current_id: str | None = None
    pending: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    regression_rescan: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Cursor":
        return cls(
            current_id=d.get("current_id"),
            pending=list(d.get("pending", [])),
            deferred=list(d.get("deferred", [])),
            done=list(d.get("done", [])),
            regression_rescan=bool(d.get("regression_rescan", False)),
        )

    def advance(self) -> str | None:
        """Mark current done, pop next pending into current. Returns new current_id."""
        if self.current_id and self.current_id not in self.done:
            self.done.append(self.current_id)
        self.current_id = self.pending.pop(0) if self.pending else None
        return self.current_id

    def pull_deferred(self, n: int = 5) -> int:
        moved = self.deferred[:n]
        self.deferred = self.deferred[n:]
        self.pending.extend(moved)
        return len(moved)

    def is_empty(self) -> bool:
        return self.current_id is None and not self.pending


@dataclass
class GateOutcome:
    verdict: Verdict
    csw_gate_status: str  # "pass" | "fail" | "unresolvable"
    pillar_verdict: dict[str, str]
    pillar_counts: dict[str, dict[str, int]]
    by_source: dict[str, int]
    regressions: list[str]

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "csw_gate_pillar": Pillar.INTENT.value,
            "csw_gate_status": self.csw_gate_status,
            "pillar_verdict": self.pillar_verdict,
            "pillar_counts": self.pillar_counts,
            "by_source": self.by_source,
            "regressions": self.regressions,
        }
