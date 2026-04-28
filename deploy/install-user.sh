#!/usr/bin/env bash
# Per-user install for review-agent v3.
#
# Run as the LOGIN USER (not root). Assumes:
#   - root has already done: apt install -y python3-venv git rsync; loginctl enable-linger <you>
#   - the repo is cloned to ~/code/review-agent (or pass a different path as $1)
#
# This script: creates venv, installs package, drops a systemd --user unit,
# enables it, and prints next-step instructions.

set -euo pipefail

CODE_DIR="${1:-$HOME/code/review-agent}"
CONFIG_DIR="$HOME/.config/review-agent"
UNIT_DIR="$HOME/.config/systemd/user"

if [ "$(id -u)" -eq 0 ]; then
    echo "do not run this as root — run as your normal user (e.g. reviewer)" >&2
    exit 2
fi

if [ ! -f "$CODE_DIR/pyproject.toml" ]; then
    echo "no pyproject.toml at $CODE_DIR — pass the repo path as \$1" >&2
    exit 2
fi

mkdir -p "$CONFIG_DIR" "$UNIT_DIR"

cd "$CODE_DIR"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -e ".[dev,ingest]" --quiet

if [ ! -f "$CONFIG_DIR/secrets.env" ]; then
    cp deploy/secrets.env.example "$CONFIG_DIR/secrets.env"
    chmod 600 "$CONFIG_DIR/secrets.env"
    echo "* Created stub $CONFIG_DIR/secrets.env — edit it next."
fi

cp deploy/systemd/review-agent-user.service "$UNIT_DIR/review-agent.service"
systemctl --user daemon-reload
systemctl --user enable review-agent.service
echo "* systemd --user unit installed and enabled."

echo
echo "Next steps:"
echo "  1) edit $CONFIG_DIR/secrets.env (DEEPSEEK_API_KEY + LARK_*)"
echo "  2) systemctl --user start review-agent"
echo "  3) curl -s http://127.0.0.1:8080/healthz"
echo "  4) $CODE_DIR/.venv/bin/review-agent doctor"
echo "  5) ask root to add a Caddy snippet routing /lark/webhook → 127.0.0.1:8080"
echo "     (see deploy/caddy/review-agent.caddy and INSTALL.md §B.4)"
