# Changelog

## [3.0.0] — 2026-04-28

First release of v3 — full rewrite. Replaces v1.x (hermes-based) and v2.x
(openclaw-based) with a standalone backend.

### Architecture
- **Standalone FastAPI backend**, no hermes / no openclaw dependency.
- Python 3.11+ • SQLite (state) + filesystem (content) • httpx (Lark + DeepSeek).
- Single uvicorn process with in-process asyncio task queue, persisted to
  SQLite so tasks survive restart.
- DeepSeek-v4-pro for review LLM (configurable; flash variant for fast stages).

### Review framework (carried over from v2.4.2 verbatim)
- 4 pillars: Background / Materials / Framework / **Intent (CSW gate, always BLOCKER)**.
- Responder Simulation top layer: LLM role-plays the Responder using their `profile.md`.
- 6 challenge dimensions horizontally: data integrity / logical consistency /
  feasibility / stakeholders / risk / ROI clarity.
- IM Q&A loop with shortcut replies `(a) accept (b) reject (c) modify (pass) (more) (done) (custom)`.
- 6-section decision-ready brief on close (议题 / 数据 / 自检 / 待决策 / 时间 / 风险).

### Hardening (driven by 3-round LLM design review)
- Single-point session path scoping (`util.path.resolve_session_path`,
  rejects `..` / absolute / out-of-scope) to prevent cross-Requester leak.
- `<user_document>` XML tag wrapping for prompt-injection defense.
- Lark v2 webhook signature verification on raw body bytes (no re-serialization).
- Per-Requester virtual queues with round-robin consumer (queue isolation;
  v0 single consumer — multi-worker is v1).
- Failed-stage state machine: `failed_stage` + `last_error`, friendly user DM,
  admin dashboard Resubmit.
- `qa_active_reopened` transition + `cursor.regression_rescan` for final-gate
  FAIL → reopen flow.
- close = single SQLite UPDATE (no fs+db cross-boundary transaction);
  archival to `_closed/YYYY-MM/` is a separate nightly cron.
- Running task crash recovery on startup (`UPDATE tasks SET status='pending'
  WHERE status='running'`).

### Tests
- 63 tests passing: intent parser, path scoping, dataclass serde,
  storage CRUD, lark signature verify, lenient JSON, QA loop full state
  machine, scan + final_gate end-to-end with FakeLLMClient, per-Requester
  queue ordering + crash recovery, prompt template render with
  `<user_document>` wrapper assertions, close chain with gate FAIL → reopen,
  responder_oid path-escape rejection.

### Deployment
- Two install paths:
  - **System install** (`deploy/install.sh`) — sudo once, runs as system user
    `review-agent`, files under `/opt`, `/etc`, `/var/lib`. Hardened systemd
    unit (PrivateTmp / ProtectSystem / SystemCallFilter etc).
  - **User install** (`deploy/install-user.sh`) — per-user, no root for the
    service itself, files under `~/`, runs via `systemctl --user` with
    linger enabled.
- See `INSTALL.md` for full walkthrough (both routes).

### Documents
- `docs/PRD.md` — functional product requirements (18 sections).
- `docs/ARCH.md` — implementation architecture (22 sections, includes
  v2/v2.1 patches from design review).
- `docs/REPORT.md` — final project report (timeline, design decisions,
  test summary, known v0 limits).

### Not in v0 (planned for v1)
- Multi-Responder (one admin can have several Responders).
- `direct` document-editing mode (modify Lark Doc / Google Doc in place).
- Email backend / group-chat trigger / Google Doc ingest.
- Multi-worker for true cross-Requester parallelism.
