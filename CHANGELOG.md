# Changelog

## [3.3.0] — 2026-05-01 (Lark hardening + Slack + carried-over hotfixes)

### New

- **Slack Socket Mode integration** (`a3d1d27`). Optional second IM platform,
  gated by `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN`. Adds `slack_dm` delivery
  backend and `review_agent/slack/` adapter (mrkdwn renderer, thread
  participation tracking).
- **Lark FeishuAdapter Tier 1+2** (PR #5):
  - HTTP retry on 429 / 5xx with exponential backoff + `Retry-After` honoring.
  - Token-expired (code 99991663) auto-invalidates and retries.
  - Inbound post rich-text parser (`review_agent/lark/parser.py`).
  - 10-minute cache for `get_user`.
  - Per-session async lock in worker dispatch (keyed on `requester_oid`).
  - `update_message` to overwrite a previously-sent Lark DM in place.
- **`REVIEW_AGENT_LARK_DOMAIN` env var** for switching between
  `open.larksuite.com` (international) and `open.feishu.cn` (China). Token +
  open-API base must match the cloud where the bot is registered.

### Fixed

- **`qa_loop` cursor exhausted no_op** (PR #4 / #3 / `a858945`). When the
  cursor is exhausted but the session is still in `qa_active` (after a
  `regression_rescan` consumes its last item), the worker now sends a
  `propose_close` DM instead of swallowing the turn into a silent no_op.
- **`get_wiki_node` default `obj_type`** now `"wiki"` instead of `"docx"`.
  Lark's `/wiki/v2/spaces/get_node` resolves the URL token, not the underlying
  storage type — passing `"docx"` returns 400 not_found even when the wiki
  page IS a docx (verified empirically against `open.larksuite.com`).
- **`IngestRejected` propagation in supplementary material flow.** The
  exception now bubbles from `_append_supplementary_material` to its three
  call sites (`_handle_subject_confirmation`, qa-active reply attachment path,
  qa-active reply text/URL path), each of which DMs the user with
  `e.user_message` and stays in the gate. Previously a rejection on the
  attachment-only path could crash the worker task.

## [3.1.1] — 2026-04-28 (Gemini multimodal fallback)

Adds Google Gemini as a third multimodal fallback alongside local
tesseract/whisper.cpp and OpenAI. Useful when admin is on Gemini's free
tier — `gemini-2.5-flash` handles image OCR + voice transcription without
needing a paid OpenAI key.

### Fallback ordering

- **Image OCR**: tesseract local → **Gemini** → OpenAI Vision → reject
- **Audio**: whisper.cpp → openai-whisper → **Gemini** → OpenAI Whisper → reject

Gemini is preferred over OpenAI when both keys are configured (free-tier
friendliness). To prefer OpenAI, just unset `GEMINI_API_KEY`.

### Configuration

- `GEMINI_API_KEY` recognized in `secrets.env` (joins existing key list).
- `REVIEW_AGENT_GEMINI_MODEL` env var (default `gemini-2.5-flash`).
  Override to `gemini-3-pro-preview` if you've enabled paid billing.

### `doctor` upgraded — full multimodal matrix in one shot

```
required ok: [DEEPSEEK_API_KEY, LARK_*, ...]
required missing: []
multimodal fallback: GEMINI_API_KEY=set · OPENAI_API_KEY=not set
multimodal local bins: tesseract=missing · whisper-cpp=missing
llm provider=deepseek key=DEEPSEEK_API_KEY ✓
```

### Test count: 140 → 147 ✅

7 new tests covering Gemini paths in both backends with mocked httpx:
- image uses Gemini when only GEMINI_API_KEY is set
- model override via REVIEW_AGENT_GEMINI_MODEL
- Gemini preferred when both keys configured
- audio uses Gemini for transcription
- "[no speech detected]" sentinel handled correctly
- and 2 more.

### Migration from v3.1.0

```bash
ssh reviewer@159.65.75.97
cd ~/code/review-agent
git pull
echo "GEMINI_API_KEY=AIza..." >> ~/.config/review-agent/secrets.env
chmod 600 ~/.config/review-agent/secrets.env
systemctl --user restart review-agent
.venv/bin/review-agent doctor   # confirms GEMINI_API_KEY=set
```

## [3.1.0] — 2026-04-28 (multimodal coverage + one-click local install)

Adds first-class support for every Lark message type a Requester might send.
Previously only text + PDF worked end-to-end; images / voice / Lark Doc URLs
either silently dropped or hit broken handlers. v3.1 makes the coverage
**complete** (no Requester message gets ignored) and gives admin **two equally
viable install paths** depending on whether they want zero-config (API) or
zero-cost (local binaries).

### 🟢 Coverage matrix (every Lark msg_type → defined behavior)

| Sent by Requester | v3.0.x | v3.1 |
|---|---|---|
| Text | ingest ✓ | ingest ✓ |
| Text + URL (any) | ingest as text (URL not followed) | URLs auto-detected → fetched as material ✨ |
| Text + Lark Doc URL | ingest as text | fetched via Lark Open API ✨ |
| Lark `post` (rich text) | dropped silently | text extracted from element tree ✨ |
| PDF file | ingest ✓ | ingest ✓ |
| Image | crashed (`.jpg` ext mismatch) | OCR via tesseract or OpenAI Vision API ✨ |
| Voice / audio | crashed (whisper output bug) | transcribe via whisper.cpp or OpenAI Whisper API ✨ |
| Other file (xlsx etc.) | crashed | friendly refuse DM ✨ |
| Video / sticker / card / share | dropped silently | friendly refuse DM ✨ |

### 🟢 Two install paths for image/voice (admin chooses)

```bash
# Path A — zero local install, pay-per-use OpenAI fallback
echo "OPENAI_API_KEY=sk-..." >> ~/.config/review-agent/secrets.env
systemctl --user restart review-agent

# Path B — one-click local binaries (no API cost)
review-agent install-multimodal           # apt/brew install tesseract + whisper.cpp
systemctl --user restart review-agent
```

`install-multimodal` auto-detects OS (apt / brew / dnf / pacman) and installs
the right packages with the Chinese OCR language pack. `--tesseract-only` for
just OCR; `--dry-run` to preview.

### 🔴 Bug fixes (v3.0.x multimodal scaffolding had 8 known bugs)

- **B1+B2 · URL flow dead-loop**: dispatcher's URL-detection branch was
  unreachable behind the plain-text branch; URLs were never followed. Fixed
  by reordering: Lark Doc URL → other URL → plain text.
- **B3 · whisper.cpp output**: `--output-txt` writes to a file, not stdout —
  reading stdout returned progress logs as the "transcription". Switched to
  `-nt` (no timestamps) + stdout proper.
- **B4 · image extension mismatch**: all images saved as `.jpg` regardless
  of actual format; magic-bytes detection added (`util/file_magic.py`).
- **B5 · async safety**: `whisper.load_model()` is blocking and was awaited
  inside an async function; wrapped with `asyncio.to_thread`.
- **B6 · whisper language hardcoded `zh`**: switched to auto-detect.
- **B7 · web_scrape bs4 ImportError**: `from bs4` was outside try/except;
  raised on systems without bs4 installed.
- **B8 · IngestRejected swallowed as fail**: dispatcher's blanket
  `except Exception` triggered `_fail_session` for every error including
  `IngestRejected` (which carries a friendly user message). Now split: 
  IngestRejected → friendly DM + cancel session; other Exception → fail.
- **+ subject_too_long fix from v3.0.1 carried forward**

### 🟢 New backends + helpers

- `pipeline/ingest_backends/lark_doc.py` — `LarkDocBackend` fetches Lark Doc
  / Wiki content via the bot's existing tenant_access_token. Pure async, no
  scraping, no extra OAuth.
- `pipeline/ingest_backends/web_scrape.py` — `WebScrapBackend.scrape_urls()`
  callable directly by dispatcher (not via mime/ext routing).
- `util/file_magic.py` — `detect_image_ext` / `detect_audio_ext` /
  `detect_file_ext` from raw bytes.
- `routers/lark_webhook.py::_extract_post_text` — walks Lark `post` element
  tree to plain text.
- 7 new catch-all polite-refuse messages for video / sticker / interactive /
  share_chat / share_user / system / unknown.

### 🛠 New CLI

- `review-agent install-multimodal [--tesseract-only] [--dry-run]` — calls
  the bundled `deploy/install-multimodal.sh`.

### 🛠 Operational

- `pyproject.toml` extras renamed: `[ingest]` → `[multimodal]` (alias kept).
- `secrets.env.example` adds `OPENAI_API_KEY=` slot with explanation.
- `install-user.sh` accepts `--multimodal-local` (also installs binaries) and
  `--no-multimodal` (text+PDF only).

### Test count: 101 → 138 ✅

37 new tests covering: file magic detection (15), LarkDocBackend (10),
dispatcher routing for every msg_type (12).

### Migration from v3.0.x

```bash
ssh reviewer@159.65.75.97
cd ~/code/review-agent
git pull
.venv/bin/pip install -e ".[multimodal]" --upgrade
systemctl --user restart review-agent
.venv/bin/review-agent doctor
```

Optional: `review-agent install-multimodal` if you want OCR + voice locally
instead of OpenAI API.

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
