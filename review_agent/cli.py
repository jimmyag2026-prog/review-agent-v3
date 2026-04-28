from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import secrets as secrets_mod
from .config import load as load_config
from .core.enums import Role, SessionStatus
from .core.models import User
from .core.storage import Storage


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser("review-agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("setup", help="initialize admin + responder")
    sp.add_argument("--admin-open-id", required=True)
    sp.add_argument("--admin-name", default="admin")
    sp.add_argument("--responder-open-id")
    sp.add_argument("--responder-name", default="responder")

    ap = sub.add_parser("add-user")
    ap.add_argument("--open-id", required=True)
    ap.add_argument("--role", required=True, choices=[r.value for r in Role])
    ap.add_argument("--responder", help="for Requester only")
    ap.add_argument("--name", default="user")

    lu = sub.add_parser("list-users")
    lu.add_argument("--role", choices=[r.value for r in Role])

    ls = sub.add_parser("list-sessions")
    ls.add_argument("--status", choices=[s.value for s in SessionStatus])

    rm = sub.add_parser("remove-user", help="delete a user record (sessions stay)")
    rm.add_argument("open_id")

    sub.add_parser("doctor")
    sub.add_parser("migrate")

    sm = sub.add_parser("set-model", help="change the LLM model used for reviews")
    sm.add_argument("model", help="e.g. deepseek-v4-pro / deepseek-v4-flash")
    sm.add_argument("--fast", action="store_true",
                    help="set the fast_model (used for confirm_topic) instead of default_model")

    sub.add_parser("show-config", help="print effective config (paths, llm, review)")

    im = sub.add_parser("install-multimodal",
                         help="one-click install: tesseract OCR + whisper.cpp (apt/brew)")
    im.add_argument("--tesseract-only", action="store_true",
                    help="OCR only (skip whisper.cpp build)")
    im.add_argument("--dry-run", action="store_true",
                    help="print what would be installed, don't actually install")

    args = p.parse_args(argv)
    # Load secrets.env into os.environ so CLI sees the same tunables (REVIEW_AGENT_MODEL etc.)
    # that the systemd service loads via EnvironmentFile=.
    _load_secrets_into_env()
    cfg = load_config()
    storage = Storage(cfg.paths.db, cfg.paths.fs)

    if args.cmd == "setup":
        admin = User(
            open_id=args.admin_open_id, display_name=args.admin_name,
            roles=[Role.ADMIN, Role.RESPONDER],
        )
        storage.upsert_user(admin)
        # default profile so reviews work out of box
        prof = Path(cfg.paths.fs) / "users" / admin.open_id / "profile.md"
        prof.parent.mkdir(parents=True, exist_ok=True)
        if not prof.exists():
            prof.write_text(_DEFAULT_PROFILE)
        if args.responder_open_id and args.responder_open_id != args.admin_open_id:
            r = User(
                open_id=args.responder_open_id, display_name=args.responder_name,
                roles=[Role.RESPONDER],
            )
            storage.upsert_user(r)
            (Path(cfg.paths.fs) / "users" / r.open_id / "profile.md").parent.mkdir(
                parents=True, exist_ok=True
            )
            (Path(cfg.paths.fs) / "users" / r.open_id / "profile.md").write_text(_DEFAULT_PROFILE)
        print(f"setup ok: admin={admin.open_id}")
        return 0

    if args.cmd == "add-user":
        roles = [Role(args.role)]
        responder_oid = args.responder if args.role == Role.REQUESTER.value else None
        u = User(
            open_id=args.open_id, display_name=args.name,
            roles=roles, pairing_responder_oid=responder_oid,
        )
        storage.upsert_user(u)
        print(f"added {args.open_id} as {args.role}")
        return 0

    if args.cmd == "remove-user":
        ok = storage.delete_user(args.open_id)
        print(f"removed {args.open_id}" if ok else f"no such user: {args.open_id}")
        return 0 if ok else 1

    if args.cmd == "list-users":
        role = Role(args.role) if args.role else None
        for u in storage.list_users(role):
            print(f"{u.open_id}\t{','.join(r.value for r in u.roles)}\t{u.display_name}")
        return 0

    if args.cmd == "list-sessions":
        status = SessionStatus(args.status) if args.status else None
        for s in storage.list_sessions(status=status):
            print(f"{s.id}\t{s.stage.value}\t{s.requester_oid}\t{s.subject or '-'}")
        return 0

    if args.cmd == "doctor":
        sec = secrets_mod.load()
        ok = []
        miss = []
        for k in ["DEEPSEEK_API_KEY", "LARK_APP_ID", "LARK_APP_SECRET",
                  "LARK_ENCRYPT_KEY", "LARK_VERIFICATION_TOKEN"]:
            (ok if k in sec else miss).append(k)
        print(f"required ok: {ok}")
        print(f"required missing: {miss}")
        # multimodal fallback keys — optional, not counted toward exit failure
        mm = []
        for k in ["GEMINI_API_KEY", "OPENAI_API_KEY"]:
            mm.append(f"{k}={'set' if k in sec else 'not set'}")
        print(f"multimodal fallback: {' · '.join(mm)}")
        # binary multimodal capabilities
        import shutil as _sh
        bins = []
        for b in ["tesseract", "whisper-cpp"]:
            p = _sh.which(b) or "not installed"
            bins.append(f"{b}={'✓' if p != 'not installed' else 'missing'}")
        print(f"multimodal local bins: {' · '.join(bins)}")
        # provider × key cross-check (round-1 I6 spirit, applied to LLM)
        provider = cfg.llm.provider.lower()
        provider_key_map = {"deepseek": "DEEPSEEK_API_KEY",
                            "openai": "OPENAI_API_KEY",
                            "anthropic": "ANTHROPIC_API_KEY"}
        expected_key = provider_key_map.get(provider)
        if expected_key:
            if expected_key in sec:
                print(f"llm provider={provider} key={expected_key} ✓")
            else:
                print(f"llm provider={provider} but {expected_key} missing ✗")
                miss.append(expected_key)
        else:
            print(f"llm provider={provider} (unknown — only deepseek implemented in v0)")
        print(f"llm default_model={cfg.llm.default_model}")
        print(f"llm fast_model={cfg.llm.fast_model}")
        try:
            storage.conn().execute("SELECT 1").fetchone()
            print("db: ok")
        except Exception as e:
            print(f"db: FAIL {e}")
        return 0 if not miss else 1

    if args.cmd == "show-config":
        print(f"config_file: {cfg.__class__.__module__}")
        print(f"secrets_file: {secrets_mod.secrets_file_path()}")
        print(f"db: {cfg.paths.db}")
        print(f"fs: {cfg.paths.fs}")
        print(f"log: {cfg.paths.log}")
        print(f"server: {cfg.server.bind}:{cfg.server.port}")
        print(f"llm.provider: {cfg.llm.provider}")
        print(f"llm.default_model: {cfg.llm.default_model}")
        print(f"llm.fast_model: {cfg.llm.fast_model}")
        print(f"llm.base_url: {cfg.llm.base_url}")
        print(f"review.max_rounds: {cfg.review.max_rounds}")
        print(f"review.top_n_findings: {cfg.review.top_n_findings}")
        return 0

    if args.cmd == "set-model":
        env_key = "REVIEW_AGENT_FAST_MODEL" if args.fast else "REVIEW_AGENT_MODEL"
        path = secrets_mod.upsert_env_value(env_key, args.model)
        print(f"[ok] {env_key}={args.model} written to {path}")
        print(f"[next] restart service so the change takes effect:")
        print(f"       systemctl --user restart review-agent")
        print(f"       (or: sudo systemctl restart review-agent)")
        return 0

    if args.cmd == "install-multimodal":
        import os
        import subprocess
        # find install-multimodal.sh next to the installed package or in repo
        here = Path(__file__).resolve().parent.parent  # review_agent/..
        candidates = [
            here / "deploy" / "install-multimodal.sh",
            Path("/opt/review-agent/deploy/install-multimodal.sh"),
            Path(os.path.expanduser("~/code/review-agent/deploy/install-multimodal.sh")),
        ]
        script = next((p for p in candidates if p.exists()), None)
        if not script:
            print("error: install-multimodal.sh not found in any of:", *candidates, sep="\n  ")
            return 2
        cmd = ["bash", str(script)]
        if args.tesseract_only:
            cmd.append("--tesseract-only")
        if args.dry_run:
            cmd.append("--dry-run")
        print(f"running: {' '.join(cmd)}")
        return subprocess.call(cmd)

    if args.cmd == "migrate":
        print("schema applied")
        return 0

    return 0


def _load_secrets_into_env() -> None:
    """Mirror the systemd EnvironmentFile= behaviour: parse secrets.env and
    push its KEY=VALUE pairs into os.environ (without overwriting anything
    already set in the shell). Lets `show-config` / `doctor` / `set-model`
    see the same effective config the daemon uses."""
    import os as _os
    path = secrets_mod.secrets_file_path()
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in _os.environ:
            _os.environ[k] = v


_DEFAULT_PROFILE = """# Responder profile

我审材料时最在意：
- 数据来源 + 日期
- Plan B
- 强反方观点

讨厌：含糊 ask / 把决策推回我 / 空话。
"""
