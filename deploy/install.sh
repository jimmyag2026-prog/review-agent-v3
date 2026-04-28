#!/usr/bin/env bash
# Install review-agent v3 on Debian/Ubuntu VPS as user `review-agent`.
# Idempotent. Re-runnable. Safe against existing /var/lib/review-agent data.
set -euo pipefail

PYBIN="${PYBIN:-/usr/bin/python3.11}"
INSTALL_DIR="${INSTALL_DIR:-/opt/review-agent}"
DATA_DIR="${DATA_DIR:-/var/lib/review-agent}"
LOG_DIR="${LOG_DIR:-/var/log/review-agent}"
ETC_DIR="${ETC_DIR:-/etc/review-agent}"
USER_NAME="${USER_NAME:-review-agent}"
SOURCE_DIR="${1:-$(pwd)}"

if [ "$(id -u)" -ne 0 ]; then
    echo "this installer must run as root (uses systemd + useradd + apt)" >&2
    exit 2
fi

if ! command -v "$PYBIN" >/dev/null 2>&1; then
    echo "python3.11 not found at $PYBIN; install it first (apt install python3.11 python3.11-venv)" >&2
    exit 2
fi

if ! id "$USER_NAME" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$DATA_DIR" --shell /usr/sbin/nologin "$USER_NAME"
fi

install -d -o "$USER_NAME" -g "$USER_NAME" "$DATA_DIR" "$LOG_DIR"
install -d -o root -g "$USER_NAME" -m 0750 "$ETC_DIR"
install -d -o "$USER_NAME" -g "$USER_NAME" "$INSTALL_DIR"

# rsync the source (skip .venv / .git)
rsync -a --delete --exclude=.venv --exclude=.git --exclude=__pycache__ \
    --exclude=.pytest_cache --exclude='*.db*' \
    "$SOURCE_DIR/" "$INSTALL_DIR/"
chown -R "$USER_NAME":"$USER_NAME" "$INSTALL_DIR"

sudo -u "$USER_NAME" "$PYBIN" -m venv "$INSTALL_DIR/.venv"
sudo -u "$USER_NAME" "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$USER_NAME" "$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"

# config.toml + secrets.env templates if missing
if [ ! -f "$ETC_DIR/config.toml" ]; then
    cat > "$ETC_DIR/config.toml" <<EOF
[server]
bind = "127.0.0.1"
port = 8080

[paths]
db = "$DATA_DIR/state.db"
fs = "$DATA_DIR/fs"
log = "$LOG_DIR"

[lark]
app_id = ""
domain = "https://open.feishu.cn"

[llm]
provider = "deepseek"
default_model = "deepseek-v4-pro"
fast_model = "deepseek-v4-flash"

[review]
max_rounds = 3
top_n_findings = 5
EOF
    chown root:"$USER_NAME" "$ETC_DIR/config.toml"
    chmod 0640 "$ETC_DIR/config.toml"
fi

if [ ! -f "$ETC_DIR/secrets.env" ]; then
    cat > "$ETC_DIR/secrets.env" <<'EOF'
DEEPSEEK_API_KEY=
LARK_APP_ID=
LARK_APP_SECRET=
LARK_VERIFICATION_TOKEN=
LARK_ENCRYPT_KEY=
EOF
    chown root:"$USER_NAME" "$ETC_DIR/secrets.env"
    chmod 0600 "$ETC_DIR/secrets.env"
    echo "* Created stub $ETC_DIR/secrets.env — fill in DEEPSEEK_API_KEY + LARK_* values"
fi

install -o root -g root -m 0644 "$INSTALL_DIR/deploy/systemd/review-agent.service" /etc/systemd/system/review-agent.service
systemctl daemon-reload

# Caddy site (only if Caddy is installed)
if command -v caddy >/dev/null 2>&1 && [ -d /etc/caddy/Caddyfile.d ]; then
    install -o root -g root -m 0644 "$INSTALL_DIR/deploy/caddy/review-agent.caddy" /etc/caddy/Caddyfile.d/review-agent.caddy
    echo "* Caddy snippet installed; remember to edit YOUR-DOMAIN and 'systemctl reload caddy'"
fi

echo "* Done. Next steps:"
echo "  1) edit $ETC_DIR/secrets.env"
echo "  2) edit $ETC_DIR/config.toml (set lark.app_id)"
echo "  3) sudo -u $USER_NAME $INSTALL_DIR/.venv/bin/review-agent setup --admin-open-id ou_xxx --admin-name 'Your name'"
echo "  4) systemctl enable --now review-agent"
echo "  5) sudo -u $USER_NAME $INSTALL_DIR/.venv/bin/review-agent doctor"
