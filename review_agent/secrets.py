"""Secrets loader: env > $REVIEW_AGENT_SECRETS_FILE > /etc/review-agent/secrets.env > macOS keychain (dev)."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ENV_FILE_DEFAULT = "/etc/review-agent/secrets.env"
SECRET_KEYS = (
    "DEEPSEEK_API_KEY",
    "LARK_APP_ID",
    "LARK_APP_SECRET",
    "LARK_VERIFICATION_TOKEN",
    "LARK_ENCRYPT_KEY",
)
# non-secret env vars that share the same env file (for convenience)
TUNABLE_ENV_KEYS = (
    "REVIEW_AGENT_MODEL",
    "REVIEW_AGENT_FAST_MODEL",
)


def secrets_file_path() -> Path:
    explicit = os.environ.get("REVIEW_AGENT_SECRETS_FILE")
    if explicit:
        return Path(explicit)
    system_path = Path(ENV_FILE_DEFAULT)
    # Fall back to user path when /etc isn't writable (typical user-mode install)
    if system_path.exists() or (hasattr(os, "geteuid") and os.geteuid() == 0):
        return system_path
    return Path(os.path.expanduser("~/.config/review-agent/secrets.env"))


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _from_keychain(service: str) -> str | None:
    if not shutil.which("security"):
        return None
    user = os.environ.get("USER", "")
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-a", user, "-s", service, "-w"],
            capture_output=True, text=True, check=False,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (FileNotFoundError, OSError):
        pass
    return None


_KEYCHAIN_SERVICE = {
    "DEEPSEEK_API_KEY": "deepseek-api-key",
    "LARK_APP_SECRET": "review-agent-lark-app-secret",
    "LARK_VERIFICATION_TOKEN": "review-agent-lark-verification-token",
    "LARK_ENCRYPT_KEY": "review-agent-lark-encrypt-key",
}


def load(env_file: str | None = None) -> dict[str, str]:
    env_path = Path(env_file or os.environ.get("REVIEW_AGENT_SECRETS_FILE", ENV_FILE_DEFAULT))
    file_vals = _read_env_file(env_path)
    out: dict[str, str] = {}
    for key in SECRET_KEYS:
        if key in os.environ and os.environ[key]:
            out[key] = os.environ[key]
        elif key in file_vals and file_vals[key]:
            out[key] = file_vals[key]
        else:
            kc = _KEYCHAIN_SERVICE.get(key)
            if kc:
                v = _from_keychain(kc)
                if v:
                    out[key] = v
    return out


def get(key: str, *, required: bool = True) -> str:
    secrets = load()
    val = secrets.get(key)
    if val:
        return val
    if required:
        raise RuntimeError(f"missing secret: {key}")
    return ""


def upsert_env_value(key: str, value: str, *, env_file: str | None = None) -> Path:
    """Write KEY=VALUE into the env file (replace existing line or append).

    Used by `review-agent set-model`. Atomic via tmp+rename. Preserves other lines + comments.
    """
    path = Path(env_file) if env_file else secrets_file_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
    else:
        lines = path.read_text().splitlines()
    new_line = f"{key}={value}"
    replaced = False
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.split("=", 1)[0].strip() == key:
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        lines.append(new_line)
    body = "\n".join(lines).rstrip() + "\n"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".tmp.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(body)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path
