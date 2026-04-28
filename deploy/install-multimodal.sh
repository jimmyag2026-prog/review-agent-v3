#!/usr/bin/env bash
# install-multimodal.sh — one-click multimodal stack for review-agent v3.1+.
#
# Installs the system binaries that make image OCR + voice transcription work
# WITHOUT relying on OpenAI's API. Skip this if you'd rather pay per-call for
# OpenAI Vision + Whisper API and just set OPENAI_API_KEY in secrets.env.
#
# Usage:
#   bash install-multimodal.sh                  # full install (tesseract + zh + whisper.cpp + base model)
#   bash install-multimodal.sh --tesseract-only # OCR only (skip whisper.cpp)
#   bash install-multimodal.sh --dry-run        # just print what'd happen
#
# Auto-detects OS:
#   - Debian/Ubuntu (apt-get)
#   - macOS         (brew)
#   - Other Linux   (instructs user)

set -euo pipefail

DRY_RUN=false
TESSERACT_ONLY=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)        DRY_RUN=true; shift ;;
        --tesseract-only) TESSERACT_ONLY=true; shift ;;
        -h|--help)
            sed -n '2,18p' "$0"
            exit 0 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

run() {
    if $DRY_RUN; then
        echo "[dry-run] $*"
    else
        echo "+ $*"
        eval "$@"
    fi
}

OS="$(uname -s)"
if [[ "$OS" == "Darwin" ]]; then
    PKG_MGR="brew"
elif [[ "$OS" == "Linux" ]]; then
    if command -v apt-get >/dev/null 2>&1; then
        PKG_MGR="apt"
    elif command -v dnf >/dev/null 2>&1; then
        PKG_MGR="dnf"
    elif command -v pacman >/dev/null 2>&1; then
        PKG_MGR="pacman"
    else
        echo "[error] no recognized package manager (apt/dnf/pacman). Install tesseract + whisper.cpp manually." >&2
        exit 3
    fi
else
    echo "[error] unsupported OS: $OS" >&2
    exit 3
fi
echo "Detected OS=$OS pkg=$PKG_MGR"
echo

# need_root: true if pkg manager needs sudo (apt/dnf/pacman do; brew does not)
NEED_ROOT=false
case "$PKG_MGR" in apt|dnf|pacman) NEED_ROOT=true ;; esac

if $NEED_ROOT && [[ $EUID -ne 0 ]] && ! command -v sudo >/dev/null 2>&1; then
    echo "[error] need root or sudo to apt/dnf/pacman install. Re-run as root." >&2
    exit 4
fi
SUDO=""
if $NEED_ROOT && [[ $EUID -ne 0 ]]; then
    SUDO="sudo"
fi

# ── 1. Tesseract OCR + Chinese language pack ──
echo "==> Installing tesseract + chi_sim language pack"
case "$PKG_MGR" in
    apt)
        run "$SUDO apt-get update -qq"
        run "$SUDO apt-get install -y tesseract-ocr tesseract-ocr-chi-sim"
        ;;
    brew)
        run "brew install tesseract tesseract-lang"
        ;;
    dnf)
        run "$SUDO dnf install -y tesseract tesseract-langpack-chi_sim"
        ;;
    pacman)
        run "$SUDO pacman -S --noconfirm tesseract tesseract-data-chi_sim"
        ;;
esac
echo

if $TESSERACT_ONLY; then
    echo "[ok] tesseract-only install complete. Skipping whisper.cpp."
    echo "Voice messages will fall back to OpenAI Whisper API (needs OPENAI_API_KEY)."
    exit 0
fi

# ── 2. whisper.cpp ──
echo "==> Installing whisper.cpp"
case "$PKG_MGR" in
    apt)
        # whisper.cpp isn't in apt; clone + build
        if ! command -v whisper-cpp >/dev/null 2>&1; then
            run "$SUDO apt-get install -y build-essential cmake git wget"
            INSTALL_DIR="/opt/whisper.cpp"
            run "$SUDO git clone https://github.com/ggerganov/whisper.cpp.git $INSTALL_DIR || true"
            run "cd $INSTALL_DIR && $SUDO make -j\$(nproc)"
            run "$SUDO ln -sf $INSTALL_DIR/main /usr/local/bin/whisper-cpp"
            run "cd $INSTALL_DIR && $SUDO bash ./models/download-ggml-model.sh base"
        else
            echo "[skip] whisper-cpp already on PATH"
        fi
        ;;
    brew)
        run "brew install whisper-cpp || true"
        # download base model
        WHISPER_DIR="${HOMEBREW_PREFIX:-/opt/homebrew}/share/whisper"
        if [[ ! -f "$WHISPER_DIR/models/ggml-base.bin" ]]; then
            run "mkdir -p \"$HOME/.whisper/models\""
            run "wget -O \"$HOME/.whisper/models/ggml-base.bin\" https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
        fi
        ;;
    dnf|pacman)
        echo "[note] whisper.cpp not in $PKG_MGR repos. Build from source:"
        echo "  git clone https://github.com/ggerganov/whisper.cpp /opt/whisper.cpp"
        echo "  cd /opt/whisper.cpp && make"
        echo "  bash ./models/download-ggml-model.sh base"
        echo "  ln -s /opt/whisper.cpp/main /usr/local/bin/whisper-cpp"
        echo "Or skip and rely on OpenAI Whisper API (set OPENAI_API_KEY)."
        ;;
esac
echo

echo "[done] multimodal stack installed."
echo "Verify:"
echo "  tesseract --version 2>&1 | head -1"
echo "  whisper-cpp --help 2>&1 | head -1 || echo '(whisper.cpp missing — falls back to OpenAI Whisper API)'"
echo
echo "Don't forget Python deps:"
echo "  pip install -e \".[multimodal]\""
echo
echo "Restart the service to pick up the new capabilities:"
echo "  systemctl --user restart review-agent  (user install)"
echo "  systemctl restart review-agent         (system install)"
