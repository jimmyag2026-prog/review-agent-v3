from __future__ import annotations

from enum import StrEnum


class Pillar(StrEnum):
    BACKGROUND = "Background"
    MATERIALS = "Materials"
    FRAMEWORK = "Framework"
    INTENT = "Intent"


class Severity(StrEnum):
    BLOCKER = "BLOCKER"
    IMPROVEMENT = "IMPROVEMENT"
    NICE_TO_HAVE = "NICE-TO-HAVE"


class FindingSource(StrEnum):
    FOUR_PILLAR = "four_pillar_scan"
    RESPONDER_SIM = "responder_simulation"
    MANUAL = "manual"


class FindingStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    MODIFIED = "modified"
    UNRESOLVABLE = "unresolvable"


class Stage(StrEnum):
    INTAKE = "intake"
    SUBJECT_CONFIRMATION = "subject_confirmation"
    SCANNING = "scanning"
    QA_ACTIVE = "qa_active"
    QA_ACTIVE_REOPENED = "qa_active_reopened"
    AWAITING_CLOSE_CONFIRMATION = "awaiting_close_confirmation"  # Issue #4
    AWAITING_FINAL_DRAFT = "awaiting_final_draft"
    MERGING = "merging"
    FINAL_GATING = "final_gating"
    CLOSING = "closing"
    CLOSED = "closed"
    INGEST_FAILED = "ingest_failed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class SessionStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Verdict(StrEnum):
    READY = "READY"
    READY_WITH_OPEN_ITEMS = "READY_WITH_OPEN_ITEMS"
    FORCED_PARTIAL = "FORCED_PARTIAL"
    FAIL = "FAIL"


class Role(StrEnum):
    ADMIN = "Admin"
    RESPONDER = "Responder"
    REQUESTER = "Requester"


class Intent(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    MODIFY = "modify"
    PASS = "pass"
    MORE = "more"
    DONE = "done"
    CUSTOM = "custom"
    QUESTION = "question"
    FORCE_CLOSE = "force_close"
    PICK_A = "pick_a"
    PICK_B = "pick_b"
    PICK_C = "pick_c"
