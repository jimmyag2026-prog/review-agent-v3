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

    sub.add_parser("doctor")
    sub.add_parser("migrate")

    args = p.parse_args(argv)
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
        print(f"ok: {ok}")
        print(f"missing: {miss}")
        # sanity: db reachable
        try:
            storage.conn().execute("SELECT 1").fetchone()
            print("db: ok")
        except Exception as e:
            print(f"db: FAIL {e}")
        return 0 if not miss else 1

    if args.cmd == "migrate":
        # schema is loaded in Storage.__init__ via CREATE TABLE IF NOT EXISTS
        print("schema applied")
        return 0

    return 0


_DEFAULT_PROFILE = """# Responder profile

我审材料时最在意：
- 数据来源 + 日期
- Plan B
- 强反方观点

讨厌：含糊 ask / 把决策推回我 / 空话。
"""
