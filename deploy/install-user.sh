#!/usr/bin/env bash
# Per-user install for review-agent v3.
#
# Run as the LOGIN USER (not root). Assumes:
#   - root has already done: apt install -y python3-venv git rsync; loginctl enable-linger <you>
#   - the repo is cloned to ~/code/review-agent (or pass a different path as $1)
#
# This script: creates venv, installs package + multimodal Python deps, drops
# a systemd --user unit, enables it, and prints next-step instructions.
#
# Flags:
#   --multimodal-local   also run install-multimodal.sh (system tesseract + whisper.cpp).
#                        Skip this if you'd rather use OpenAI Vision/Whisper API
#                        as the OCR/audio fallback (just set OPENAI_API_KEY in secrets.env).
#   --no-multimodal      skip the [multimodal] Python deps too (text+PDF only)

set -euo pipefail

WITH_MULTIMODAL_LOCAL=false
WITH_MULTIMODAL_PY=true
POSITIONAL=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --multimodal-local) WITH_MULTIMODAL_LOCAL=true; shift ;;
        --no-multimodal)    WITH_MULTIMODAL_PY=false; shift ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        *) POSITIONAL+=("$1"); shift ;;
    esac
done
set -- "${POSITIONAL[@]:-}"

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
if $WITH_MULTIMODAL_PY; then
    .venv/bin/pip install -e ".[dev,multimodal]" --quiet
    echo "* installed review-agent + dev + multimodal Python deps"
else
    .venv/bin/pip install -e ".[dev]" --quiet
    echo "* installed review-agent + dev (NO multimodal Python deps — text + PDF only)"
fi

if $WITH_MULTIMODAL_LOCAL; then
    if [ -x deploy/install-multimodal.sh ]; then
        echo "==> installing local multimodal binaries (tesseract + whisper.cpp)"
        bash deploy/install-multimodal.sh
    else
        echo "[warn] deploy/install-multimodal.sh not found, skipping local-binary install" >&2
    fi
fi

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
echo "  1) edit $CONFIG_DIR/secrets.env (DEEPSEEK_API_KEY + LARK_* required;"
echo "     optionally OPENAI_API_KEY as fallback for image OCR + audio transcription)"
echo "  2) systemctl --user start review-agent"
echo "  3) curl -s http://127.0.0.1:8080/healthz"
echo "  4) $CODE_DIR/.venv/bin/review-agent doctor"
echo "  5) ask root to add a Caddy snippet routing /lark/webhook → 127.0.0.1:8080"
echo "     (see deploy/caddy/review-agent.caddy and INSTALL.md §B.4)"
if ! $WITH_MULTIMODAL_LOCAL; then
    echo
    echo "ℹ multimodal: image OCR / audio will use OpenAI API (set OPENAI_API_KEY) by default."
    echo "  To install local tesseract + whisper.cpp instead (avoid API costs),"
    echo "  run: $CODE_DIR/.venv/bin/review-agent install-multimodal"
fi
