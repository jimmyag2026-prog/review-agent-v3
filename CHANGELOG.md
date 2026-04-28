# Changelog

## [3.0.1] — 2026-04-28 (live-test bug fixes)

Series of fixes from the first multi-user end-to-end live test on the VPS
(reviewer user, two real Lark accounts: Admin + Requester). 7 bugs found
in production behavior, all hardened with regression tests. 38 new tests
(63 → 101 total). All fixes deployed and verified live.

### 🔴 Fixed — state-machine deadlocks

- **`AWAITING_CLOSE_CONFIRMATION` stage** (Issue #4) — after the Requester
  accepted the last BLOCKER, `qa_loop` returned `propose_close` and the
  dispatcher sent a "BLOCKER 都闭合 ✅ close 还是 more?" DM, but **did not
  change `session.stage`**. The next reply (e.g. "a") hit `qa_loop.handle_reply`
  with `cursor.current_id == None` → `no_op` → silent dead-end. Tester sent
  4 `a`s and "发过去了吗" with no response. Added explicit
  `Stage.AWAITING_CLOSE_CONFIRMATION` + dedicated handler that interprets
  `a/done` → trigger close chain, `b/more` → pull deferred, anything else →
  nudge with the option menu.

- **`SCANNING/MERGING/FINAL_GATING/CLOSING` interrupt recovery** (Issue #5
  Bug A) — these stages execute LLM calls inline (awaited inside the
  message handler). A service restart mid-stage left `session.stage = SCANNING`
  but no scan ever ran; subsequent inbound messages fell through to a generic
  "当前 session 在 X" reply with no recovery. Added `BUSY_STAGES` table in
  dispatcher: detect missing `llm_calls` row for the expected stage → friendly
  "上次被打断了，重新跑" DM + actually restart the stage.

### 🔴 Fixed — silent crashes

- **LLM empty-content / non-JSON crash worker → session stuck** (Issue #6)
  — `pipeline/_json.extract()` raised `ValueError` on bad LLM output, which
  propagated past dispatcher's `except LLMTerminalFailure` handler and got
  caught by the worker as a generic terminal failure. The session was left
  forever at `SCANNING` without `_fail_session` running, so the Requester
  never saw a friendly "卡了，admin 已收通知" message. Introduced
  `LLMOutputParseError(LLMTerminalFailure)` so dispatcher's existing handler
  catches it cleanly.

- **Token-budget too tight** (root cause of the empty-content failures
  above) — `scan_four_pillar` was called with `max_tokens=4096`. DeepSeek
  reasoning models can spend 3-4k tokens just thinking before they emit any
  JSON; the entire budget went to reasoning and `content` came back empty
  with `finish_reason: length`. Bumped `max_tokens`:
  - `scan_four_pillar`: 4096 → **8192**
  - `scan_responder_sim`: 2048 → **4096**
  - `qa_emit_finding`: 1024 → **2048**
  - `final_gate`: 2048 → **4096**
  - `build_summary`: 4096 → **6144**

  This applies to BOTH `deepseek-v4-pro` and `deepseek-v4-flash` — initial
  diagnosis ("flash too weak") was wrong; flash handles the prompt fine
  with adequate budget.

### 🟡 Improved — UX

- **Auto-register first-DM tutorial** (Issue #7c) — auto-register's welcome
  message was full of jargon ("4 柱框架挑刺、Q&A 你直到材料 decision-ready,
  6 节 brief"). Rewritten as a tutorial:
  > 你好 [name] 👋
  > 我是 [admin] 的会前 review 助手 — 帮你把要给 [admin] 看的东西先过一遍...
  > 怎么用：
  > ① 把要给 [admin] 看的东西发我（草稿、提案、文字 / PDF / Lark 文档都行）
  > ② 我会用 [admin] 的眼光挑几个问题问你
  > ③ 你回复我（一般是 a / b / c / pass / done），几轮后我整理 brief 给 [admin]

- **Findings format de-jargonified** (Issue #7a, b) — old format exposed
  internal labels:
  ```
  🔴【BLOCKER · 📊 Materials · r3】
  R1/3 · 来源 你模拟 · 待 4+8
  问题 ...
  ```
  Tester couldn't read it. New format:
  ```
  🔴  必须修一下
  问题  ...
  建议  ...
  ─────
  回复：a 改 · b 不同意 · c 我有自己的版本 · pass 跳 · done 够了
  ```
  Severity → Chinese label (BLOCKER → "必须修一下" / IMPROVEMENT → "建议改一下"
  / NICE-TO-HAVE → "可选"). pillar / id / source / round are persisted in
  `annotations.jsonl` for audit but no longer shown to Requester.

- **Subject truncation** (Issue #5 Bug B) — when Requester answered
  `confirm_topic` with a long custom paste (e.g. re-sent the full meetup
  material), the entire 1000+ char text became `session.subject`. Added
  `_trim_subject()` which keeps the first sentence / line / ≤60 chars.

### 🟢 Added — operational ergonomics

- **`set-model` CLI** (Issue #1) — change the LLM model without editing
  config files. Writes `REVIEW_AGENT_MODEL` (or `REVIEW_AGENT_FAST_MODEL`
  with `--fast`) into `secrets.env`. Auto-detects user vs system install
  paths.
  ```
  review-agent set-model deepseek-v4-flash
  review-agent set-model deepseek-v4-flash --fast
  ```

- **`show-config` CLI** — print the effective config (paths, llm provider /
  models, base_url, review tunables). CLI also auto-loads `secrets.env` so
  tunables like `REVIEW_AGENT_MODEL` apply without restarting the daemon.

- **`doctor` upgraded** — checks LLM provider × API-key match, prints
  effective model names. Catches "I set provider=openai but only have
  DEEPSEEK_API_KEY" type misconfigs early.

- **`remove-user` CLI** — delete a user record (with FK bypass so cancelled
  sessions can stay for audit).

- **`auto_register_requesters` config flag** (default `true`) — turn off
  if you want whitelist-only mode.

### 🛠 Internals

- `Session` model gained 3 fields (`admin_style`, `review_rules`,
  `responder_profile`) populated from frozen fs files at load time.
- `Storage.has_llm_call_for_stage(session_id, stage)` — used by busy-stage
  recovery to distinguish "in progress" from "interrupted".
- Multimodal ingest backends added (`ImageBackend`, `AudioBackend`,
  `WebScrapBackend`) and `LarkClient` extended with `download_attachment`,
  `get_doc_raw`, `get_wiki_node`, `append_doc_blocks` — initial scaffolding,
  full integration in v3.1.

### Test count: 63 → 101 ✅

Full debug log of the live test session lives in
[`docs/v301-live-test-debuglog.md`](docs/v301-live-test-debuglog.md).

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
