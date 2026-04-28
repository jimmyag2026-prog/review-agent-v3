# review-agent v3

**Async pre-meeting review coach for Lark.** Standalone FastAPI + SQLite +
DeepSeek backend that runs on a single Linux VPS. No hermes, no openclaw —
this is the v3 rewrite (see [`docs/REPORT.md`](docs/REPORT.md) for the
design history with v1 / v2).

When a Requester DMs the bot with a draft / proposal / 1:1 agenda, the bot
challenges them through the **4-pillar framework** (Background / Materials /
Framework / **Intent — CSW gate**) plus a **Responder Simulation** layer that
role-plays the assigned Responder using their `profile.md`. After a Q&A loop,
the bot ships a **6-section decision-ready brief** to the Responder.

> Theoretical root: 1942 US Army doctrine of **Completed Staff Work** — "the
> chief only signs yes or no; all the thinking has been done by staff."

## What's in the box

| | |
|---|---|
| Runtime | Python 3.11+ • FastAPI • uvicorn • httpx • SQLite (WAL) |
| LLM | DeepSeek-v4-pro (default) • DeepSeek-v4-flash (fast stages) |
| Channel | Lark / Feishu (DM + Lark Doc output) |
| Storage | SQLite for state machine • filesystem (jsonl + markdown) for content |
| Tests | 63 unit + integration tests, no network needed (FakeLLMClient) |
| Deploy | systemd unit (system or `--user`) + Caddy reverse-proxy snippet |

## Quick start (local dev on macOS / Linux)

```bash
git clone https://github.com/jimmyag2026-prog/review-agent-v3
cd review-agent-v3
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,ingest]"

# put your DeepSeek key in env or macOS keychain
export DEEPSEEK_API_KEY=sk-...
# OR (mac):
security add-generic-password -a "$USER" -s deepseek-api-key -w "sk-..."

pytest                       # 63 tests, no network
python -m review_agent doctor
```

## VPS deploy

See [**INSTALL.md**](INSTALL.md). Two routes:

- **B (recommended for shared VPS)** — install under a regular login user
  via `systemctl --user`. Daemon runs as that user, files under `~/`. Root
  only needed once for `adduser` / `loginctl enable-linger` / Caddy.
- **A (single-tenant VPS)** — `bash deploy/install.sh` as root, daemon
  runs as system user `review-agent`, files under `/opt` / `/etc` / `/var/lib`.

After install, register your Lark Self-Built App (scopes: `im:message`,
`im:message:send_as_bot`, `im:resource`, `contact:user.id:readonly`,
`docx:document`), point its event-subscription URL at
`https://<your-host>/lark/webhook`, and DM the bot.

## Architecture

The full design lives in [`docs/ARCH.md`](docs/ARCH.md). Highlights:

- **Single uvicorn process** with an in-process asyncio task queue
  persisted in SQLite (survives restart; running tasks are recovered to
  `pending` on startup so handlers can be safely re-driven).
- **Per-Requester virtual queues** with a single round-robin consumer (queue
  isolation; v0 stays single-consumer — multi-worker is v1).
- **Path scoping** through `util/path.py::resolve_session_path()` — every
  fs read / write / subprocess call goes through it, raising on `..` /
  absolute paths or any escape from the per-Requester session dir.
- **Lark v2 webhook signature** verified on the raw body bytes (no
  re-serialization), with AES-256-CBC decrypt for encrypted events.
- **Prompt-injection defense**: every user-provided document is wrapped in
  `<user_document>...</user_document>` and the persona prompt instructs the
  model to treat tag content as data only.
- **Failed-stage state machine**: if the LLM hits terminal failure in any
  stage, the session moves to `failed` with `failed_stage` + sanitized
  `last_error`, and the Requester gets a friendly DM. Admin can Resubmit.
- **CSW gate**: Intent-pillar fail blocks session close. final_gate FAIL
  reopens Q&A via `qa_active_reopened` + `cursor.regression_rescan` until
  fail_count exceeds the configured cap, then forces `FORCED_PARTIAL`.

## Project history

- [`docs/PRD.md`](docs/PRD.md) — functional product requirements.
- [`docs/ARCH.md`](docs/ARCH.md) — implementation architecture (with
  v2/v2.1 patches from the design review).
- [`docs/REPORT.md`](docs/REPORT.md) — final report covering timeline,
  three-round LLM design review, and known v0 limits.

Earlier iterations (kept for reference, not maintained):
- v1 (hermes-based): https://github.com/jimmyag2026-prog/review-agent
- v2 (openclaw-based): https://github.com/jimmyag2026-prog/review-agent-skill

## License

MIT — see [LICENSE](LICENSE).
