# INSTALL — review-agent v3 on Linux VPS

Two install routes. Pick one:

| | A. System install | B. User install (recommended for shared VPS) |
|---|---|---|
| Process owner | system user `review-agent` | your regular login user (e.g. `reviewer`) |
| Service | `systemctl` (system-wide) | `systemctl --user` |
| Code path | `/opt/review-agent` | `~/code/review-agent` |
| Data path | `/var/lib/review-agent` | `~/.review-agent` |
| Config path | `/etc/review-agent` | `~/.config/review-agent` |
| Root needed | once for install | once for `adduser` + `loginctl enable-linger` + Caddy |
| Coexists with other apps | yes | yes (better isolation) |

Both routes end with the daemon **not** running as root. B is recommended when
the VPS already runs other services (e.g. openclaw) and you want maximum
isolation.

This guide covers both. The B route below is what we actually deploy and is
documented in more detail.

---

## Prerequisites

- Linux VPS with **Ubuntu 22.04+ / Debian 12+** (24.04 tested)
- `python3.11+` available (Ubuntu 24.04 ships 3.12, fine)
- Root or sudo for first-time setup only (creating users / installing system packages / Caddy)
- HTTPS endpoint reachable from internet (Lark webhook needs it). Bare-IP is
  fine if you have a Let's Encrypt cert via Caddy.
- A DeepSeek API key (https://platform.deepseek.com)
- A Lark Self-Built App with the right scopes (see `Lark Setup` section below)

---

## Route B — User install (recommended)

Replace `reviewer` and `159.65.75.97` with your username and host.

### B.1  One-time root setup

```bash
ssh root@159.65.75.97

# 1) install python venv support if not present
apt-get update
apt-get install -y python3-venv git rsync

# 2) create the user, enable user-level systemd survival across logout
adduser --disabled-password --gecos "" reviewer
loginctl enable-linger reviewer

# 3) authorize your SSH key for the new user
mkdir -p /home/reviewer/.ssh
chmod 700 /home/reviewer/.ssh
cp /root/.ssh/authorized_keys /home/reviewer/.ssh/authorized_keys
chown -R reviewer:reviewer /home/reviewer/.ssh
chmod 600 /home/reviewer/.ssh/authorized_keys

exit
```

### B.2  Install as your new user

```bash
ssh reviewer@159.65.75.97

# 1) source code
mkdir -p ~/code
git clone https://github.com/jimmyag2026-prog/review-agent-v3 ~/code/review-agent
cd ~/code/review-agent

# 2) python venv + deps
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev,ingest]"

# 3) (recommended) run the test suite to verify the install
.venv/bin/pytest -q
# expect: 63 passed

# 4) prepare config + secrets
mkdir -p ~/.config/review-agent ~/.config/systemd/user
cp deploy/secrets.env.example ~/.config/review-agent/secrets.env
chmod 600 ~/.config/review-agent/secrets.env
$EDITOR ~/.config/review-agent/secrets.env  # fill DEEPSEEK + LARK values

# 5) systemd --user unit
cp deploy/systemd/review-agent-user.service ~/.config/systemd/user/review-agent.service
systemctl --user daemon-reload
systemctl --user enable --now review-agent
systemctl --user status review-agent --no-pager
```

### B.3  Smoke test

```bash
# /healthz on the local port
curl -s http://127.0.0.1:8080/healthz
# {"ok":true,"version":"3.0.0"}

# verify all configured secrets
.venv/bin/review-agent doctor
```

### B.4  Public HTTPS (Caddy reverse proxy — root, one-time)

If you have an existing Caddy on the VPS using a single Caddyfile, add a
path-based route so your existing app keeps the catch-all and review-agent
gets `/lark/webhook` and `/healthz`:

```caddy
your.domain.example {     # or bare IP if using Let's Encrypt shortlived
    tls {
        issuer acme {
            dir https://acme-v02.api.letsencrypt.org/directory
            profile shortlived
        }
    }

    # review-agent routes
    @ra path /lark/webhook /healthz
    reverse_proxy @ra localhost:8080

    # everything else → existing app
    reverse_proxy localhost:18789
}
```

Validate and reload:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

If you do not have Caddy and prefer a per-user tunnel without root, install
`cloudflared` and run a tunnel as the `reviewer` user (out of scope here).

### B.5  Lark Self-Built App

In https://open.feishu.cn:

1. Create a Self-Built App (separate from any other bot you run on the same VPS).
2. Required scopes: `im:message`, `im:message:send_as_bot`, `im:resource`,
   `contact:user.id:readonly`, `docx:document`.
3. Event subscription URL: `https://your.domain.example/lark/webhook`
4. Subscribe to event `im.message.receive_v1`.
5. Copy `App ID`, `App Secret`, `Verification Token`, `Encrypt Key` into
   `~/.config/review-agent/secrets.env` on the VPS.

Restart so the service picks up the new secrets:

```bash
systemctl --user restart review-agent
```

Back in Lark Open Platform, click "Verify URL". Should turn green.

### B.6  Register yourself as Admin / Responder

You need your Lark `open_id`. Fastest way: DM the bot any text once, then on
the VPS:

```bash
journalctl --user -u review-agent -n 30 | grep sender_oid
# look for ou_xxxxxxxxxxxxxxx
```

Then:

```bash
.venv/bin/review-agent setup --admin-open-id ou_xxx --admin-name "Your Name"
.venv/bin/review-agent list-users
```

### B.6.1  Auto-register Requesters (default ON)

Once the Admin is set up, **anyone in the Lark tenant who can see the bot**
will be auto-registered as a Requester paired to the Admin's pairing
Responder when they first DM the bot. They get a welcome message; the
Admin gets a notification DM.

If you want a whitelist-only mode (manually add each Requester via
`add-user`), turn it off in `~/.config/review-agent/secrets.env`:

```
REVIEW_AGENT_AUTO_REGISTER=false
```

Then `systemctl --user restart review-agent`.

To remove an auto-registered user:

```bash
.venv/bin/review-agent remove-user ou_xxxxxxxxxxxxxxx
```

### B.6.2  Change the LLM model

v0 only ships a DeepSeek client (`llm/deepseek.py`); switching providers
(OpenAI / Anthropic / OpenRouter) is a v3.1 roadmap item that requires a new
client class and wiring in `app.py`. **Within DeepSeek**, you can switch
between `deepseek-v4-pro` (default, reasoning model, 30-90s) and
`deepseek-v4-flash` (faster, lower quality):

```bash
# default model used for scan / merge / final_gate / build_summary
.venv/bin/review-agent set-model deepseek-v4-flash

# fast model used for short stages (confirm_topic)
.venv/bin/review-agent set-model deepseek-v4-flash --fast

# show effective config
.venv/bin/review-agent show-config

# verify provider × API-key match
.venv/bin/review-agent doctor

# apply the change
systemctl --user restart review-agent
```

`set-model` writes `REVIEW_AGENT_MODEL=…` (or `REVIEW_AGENT_FAST_MODEL=…`)
into `secrets.env`, which overrides the value in `config.toml`.

To revert to defaults: open `secrets.env` and delete the
`REVIEW_AGENT_MODEL=` / `REVIEW_AGENT_FAST_MODEL=` line.

### B.6.3  Multimodal (image OCR / voice / Lark Doc URL / web URL)

Since v3.1, requesters can send any of these and the bot will use them as
review material:

| Sent | Default behavior | How it works |
|---|---|---|
| Text | Direct ingest | Always works |
| Lark Doc URL (`https://*.feishu.cn/docx/*`) | Fetch via Lark Open API | Always works (uses bot's existing Lark token) |
| Lark Wiki URL | Fetch via Lark Open API | Always works |
| Other URL (blog/Notion/etc.) | Scrape readable body | Always works (pure-Python `[multimodal]` deps) |
| PDF | Extract text via pdfminer | Always works (`[multimodal]` deps include pdfminer.six) |
| Lark `post` (rich text) | Extract plain text | Always works |
| Image (PNG/JPG/etc.) | OCR | needs **either** local tesseract **or** OpenAI Vision API |
| Voice / audio | Transcribe | needs **either** local whisper.cpp **or** OpenAI Whisper API |
| Video / sticker / card / share | Polite refuse with explanation | n/a |

**Choose one of two paths** for image OCR + voice transcription:

#### Path A — API fallback (zero local install, pay per use)

Add an OpenAI key to `~/.config/review-agent/secrets.env`:

```
OPENAI_API_KEY=sk-proj-...
```

Then `systemctl --user restart review-agent`. Image and audio will be
processed by GPT-4o-mini Vision + Whisper-1 API respectively. Cost is
~$0.001 / image, ~$0.006 / audio minute.

#### Path B — Local install (one-click, no API costs)

```bash
ssh reviewer@159.65.75.97
~/code/review-agent/.venv/bin/review-agent install-multimodal
```

Auto-detects OS (apt / brew / dnf / pacman) and installs:
- `tesseract` + `tesseract-ocr-chi-sim` (Chinese OCR)
- `whisper.cpp` + base model (speech-to-text)

Once installed, `systemctl --user restart review-agent`. Image and audio
are now processed locally — no OpenAI key needed, no per-call cost.

**Path A vs Path B**: A is simpler for low volume; B saves cost at scale.
You can also do **both** — local binaries run by default; OpenAI API is the
fallback if local fails for any reason. Just set both `OPENAI_API_KEY` and
run `install-multimodal`.

#### Verify

```bash
~/code/review-agent/.venv/bin/review-agent doctor
# under "llm": confirms model + key
# under tesseract / whisper hints: surfaces which fallback path is live
```

### B.7  First end-to-end review

DM the bot a draft (text or PDF). The flow:

1. Bot saves your text → ingest → normalize.
2. Bot proposes 2-4 candidate topics; reply `a` / `b` / `c` / `custom <自由文本>`.
3. Bot runs 4-pillar scan + Responder simulation; emits the top finding with
   options `(a) accept (b) reject (c) modify (pass) (more) (done) (custom)`.
4. Loop until all BLOCKERs are closed.
5. Bot generates a 6-section summary, creates a Lark Doc, DMs the link to
   both you (Requester) and the Responder.

### B.8  Day-to-day commands

```bash
ssh reviewer@159.65.75.97
systemctl --user status review-agent
systemctl --user restart review-agent
systemctl --user stop review-agent
systemctl --user start review-agent
journalctl --user -u review-agent -f          # tail logs
~/code/review-agent/.venv/bin/review-agent doctor
~/code/review-agent/.venv/bin/review-agent list-users
~/code/review-agent/.venv/bin/review-agent list-sessions
```

### B.9  Update

```bash
ssh reviewer@159.65.75.97
cd ~/code/review-agent
git pull
.venv/bin/pip install -e ".[dev,ingest]" --upgrade
systemctl --user restart review-agent
.venv/bin/review-agent doctor
```

### B.10  Uninstall (full cleanup of the user install)

```bash
ssh reviewer@159.65.75.97
systemctl --user disable --now review-agent
rm ~/.config/systemd/user/review-agent.service
systemctl --user daemon-reload

# back up your data first if you might want it
tar -czf ~/review-agent-data-backup-$(date +%Y%m%d).tgz ~/.review-agent ~/.config/review-agent

rm -rf ~/code/review-agent ~/.review-agent ~/.config/review-agent
exit

# root: revoke the user (optional)
ssh root@159.65.75.97
loginctl disable-linger reviewer
deluser --remove-home reviewer
```

---

## Route A — System install (single-tenant VPS)

If the VPS is dedicated to review-agent, the system install is also fine. It
creates a system user `review-agent` and installs the daemon under
`/opt/review-agent` with files under `/etc/review-agent` and
`/var/lib/review-agent`.

```bash
ssh root@<vps>
git clone https://github.com/jimmyag2026-prog/review-agent-v3 /opt/review-agent-src
cd /opt/review-agent-src
bash deploy/install.sh
$EDITOR /etc/review-agent/secrets.env
$EDITOR /etc/review-agent/config.toml          # optional; defaults are fine
sudo -u review-agent /opt/review-agent/.venv/bin/review-agent setup \
    --admin-open-id ou_xxx --admin-name "Your Name"
systemctl enable --now review-agent
sudo -u review-agent /opt/review-agent/.venv/bin/review-agent doctor
```

Caddy / Lark App / first-review steps are the same as in Route B.

Uninstall:

```bash
sudo bash /opt/review-agent-src/deploy/uninstall.sh           # keep user data
sudo bash /opt/review-agent-src/deploy/uninstall.sh --purge   # also wipe + tar backup
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `python3 -m venv .venv` fails: `ensurepip is not available` | Debian/Ubuntu's split python | `apt install python3-venv` (or `python3.12-venv`) |
| `systemctl --user` fails after logout | linger not enabled | as root: `loginctl enable-linger reviewer` |
| `/healthz` works locally but Lark "Verify URL" fails | Caddy not reloading the new route, or signature verify rejecting | `sudo caddy validate ...` then `systemctl reload caddy`; check `journalctl --user -u review-agent -n 50` for the bad-signature line |
| Bot replies "Hi, I'm review-agent. Reach out to my admin" | Sender not registered | `review-agent add-user --open-id ou_xxx --role Requester --responder ou_admin --name "Their Name"` |
| `LARK_*` shows missing in `doctor` | secrets.env not loaded | check `EnvironmentFile=` in the unit, restart the service, re-run `doctor` |
| Long LLM call blocks other Requesters | v0 single-worker (known limit) | wait for v1 multi-worker |
| Session stuck in `failed` | LLM hit terminal failure | from the dashboard or CLI, "Resubmit" the session |

For deeper debug:

```bash
journalctl --user -u review-agent -n 200 --no-pager
sqlite3 ~/.review-agent/state.db ".tables"
sqlite3 ~/.review-agent/state.db "SELECT id,stage,status,verdict,fail_count FROM sessions ORDER BY started_at DESC LIMIT 10"
```

---

## What this install does NOT do

- Set up DNS for you.
- Configure Caddy / nginx / cloudflared for you (we only provide a snippet).
- Touch any other application running on the same VPS (openclaw, hermes,
  memoirist, etc.) — they continue running unchanged.
