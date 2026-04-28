from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from ..util.ids import now_iso, ulid
from ..util.path import atomic_write
from .enums import Role, SessionStatus, Stage, Verdict
from .models import Cursor, Finding, Session, User

_SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


class Storage:
    def __init__(self, db_path: str | Path, fs_root: str | Path):
        self.db_path = str(db_path)
        self.fs_root = Path(fs_root)
        self.fs_root.mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_schema()

    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path, isolation_level=None, check_same_thread=False
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _init_schema(self) -> None:
        c = self.conn()
        c.executescript(_SCHEMA)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── users ─────────────────────────────────────────────
    def upsert_user(self, user: User) -> None:
        ts = now_iso()
        if not user.created_at:
            user.created_at = ts
        user.updated_at = ts
        roles_json = json.dumps([r.value for r in user.roles])
        self.conn().execute(
            """INSERT INTO users(open_id,display_name,roles,pairing_responder_oid,created_at,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(open_id) DO UPDATE SET
                 display_name=excluded.display_name,
                 roles=excluded.roles,
                 pairing_responder_oid=excluded.pairing_responder_oid,
                 updated_at=excluded.updated_at""",
            (
                user.open_id, user.display_name, roles_json,
                user.pairing_responder_oid, user.created_at, user.updated_at,
            ),
        )

    def get_user(self, open_id: str) -> User | None:
        row = self.conn().execute(
            "SELECT * FROM users WHERE open_id=?", (open_id,)
        ).fetchone()
        if not row:
            return None
        return User(
            open_id=row["open_id"],
            display_name=row["display_name"],
            roles=[Role(r) for r in json.loads(row["roles"])],
            pairing_responder_oid=row["pairing_responder_oid"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_users(self, role: Role | None = None) -> list[User]:
        rows = self.conn().execute("SELECT open_id FROM users").fetchall()
        users = [self.get_user(r["open_id"]) for r in rows]
        users = [u for u in users if u]
        if role:
            users = [u for u in users if u.has_role(role)]
        return users

    # ── sessions ──────────────────────────────────────────
    def create_session(
        self,
        *,
        requester_oid: str,
        responder_oid: str,
        admin_style: str,
        review_rules: str,
        responder_profile: str,
    ) -> Session:
        sid = ulid()
        fs_path = self.fs_root / "users" / requester_oid / "sessions" / sid
        fs_path.mkdir(parents=True, exist_ok=True)
        (fs_path / "input").mkdir(exist_ok=True)
        (fs_path / "final").mkdir(exist_ok=True)
        # frozen config snapshots
        atomic_write(fs_path / "admin_style.md", admin_style)
        atomic_write(fs_path / "review_rules.md", review_rules)
        atomic_write(fs_path / "profile.md", responder_profile)
        atomic_write(fs_path / "annotations.jsonl", "")
        atomic_write(fs_path / "conversation.jsonl", "")
        atomic_write(fs_path / "dissent.md", "# Dissent log\n")
        atomic_write(fs_path / "cursor.json", json.dumps(Cursor().to_dict()))

        session = Session(
            id=sid,
            requester_oid=requester_oid,
            responder_oid=responder_oid,
            fs_path=str(fs_path),
            started_at=now_iso(),
        )
        self.conn().execute(
            """INSERT INTO sessions(id,requester_oid,responder_oid,subject,stage,status,
                                    round_no,fs_path,started_at,trigger_source,fail_count,meta)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sid, requester_oid, responder_oid, None,
                Stage.INTAKE.value, SessionStatus.ACTIVE.value,
                1, str(fs_path), session.started_at, "dm", 0, json.dumps({}),
            ),
        )
        return session

    def get_session(self, session_id: str) -> Session | None:
        row = self.conn().execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_session(row)

    def get_active_session_for(self, requester_oid: str) -> Session | None:
        row = self.conn().execute(
            "SELECT * FROM sessions WHERE requester_oid=? AND status=? "
            "ORDER BY started_at DESC LIMIT 1",
            (requester_oid, SessionStatus.ACTIVE.value),
        ).fetchone()
        return self._row_to_session(row) if row else None

    def update_session(self, session_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = []
        for v in fields.values():
            if hasattr(v, "value"):
                vals.append(v.value)
            elif isinstance(v, dict):
                vals.append(json.dumps(v))
            else:
                vals.append(v)
        vals.append(session_id)
        self.conn().execute(f"UPDATE sessions SET {cols} WHERE id=?", vals)

    def list_sessions(
        self, *, requester_oid: str | None = None,
        responder_oid: str | None = None, status: SessionStatus | None = None,
    ) -> list[Session]:
        q = "SELECT * FROM sessions WHERE 1=1"
        args: list[Any] = []
        if requester_oid:
            q += " AND requester_oid=?"; args.append(requester_oid)
        if responder_oid:
            q += " AND responder_oid=?"; args.append(responder_oid)
        if status:
            q += " AND status=?"; args.append(status.value)
        q += " ORDER BY started_at DESC"
        rows = self.conn().execute(q, args).fetchall()
        return [self._row_to_session(r) for r in rows]

    def _row_to_session(self, row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            requester_oid=row["requester_oid"],
            responder_oid=row["responder_oid"],
            subject=row["subject"],
            stage=Stage(row["stage"]),
            status=SessionStatus(row["status"]),
            round_no=row["round_no"],
            fs_path=row["fs_path"],
            started_at=row["started_at"],
            closed_at=row["closed_at"],
            verdict=Verdict(row["verdict"]) if row["verdict"] else None,
            trigger_source=row["trigger_source"] or "dm",
            failed_stage=Stage(row["failed_stage"]) if row["failed_stage"] else None,
            last_error=row["last_error"],
            fail_count=row["fail_count"],
            meta=json.loads(row["meta"]) if row["meta"] else {},
        )

    # ── findings (jsonl primary, db only for cursor counts) ──
    def append_finding(self, session: Session, finding: Finding) -> None:
        path = Path(session.fs_path) / "annotations.jsonl"
        line = json.dumps(finding.to_jsonl(), ensure_ascii=False)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def load_findings(self, session: Session) -> list[dict]:
        path = Path(session.fs_path) / "annotations.jsonl"
        if not path.exists():
            return []
        out = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw:
                out.append(json.loads(raw))
        return out

    def update_finding_status(
        self, session: Session, finding_id: str, **patch: Any
    ) -> None:
        path = Path(session.fs_path) / "annotations.jsonl"
        rows = self.load_findings(session)
        for row in rows:
            if row.get("id") == finding_id:
                row.update(patch)
        atomic_write(
            path,
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        )

    # ── cursor ────────────────────────────────────────────
    def load_cursor(self, session: Session) -> Cursor:
        path = Path(session.fs_path) / "cursor.json"
        if not path.exists():
            return Cursor()
        return Cursor.from_dict(json.loads(path.read_text()))

    def save_cursor(self, session: Session, cursor: Cursor) -> None:
        path = Path(session.fs_path) / "cursor.json"
        atomic_write(path, json.dumps(cursor.to_dict(), ensure_ascii=False, indent=2))

    # ── conversation log ─────────────────────────────────
    def log_conversation(
        self, session: Session, *, role: str, text: str, intent: str | None = None
    ) -> None:
        path = Path(session.fs_path) / "conversation.jsonl"
        line = json.dumps(
            {"ts": now_iso(), "role": role, "text": text, "intent": intent},
            ensure_ascii=False,
        )
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def append_dissent(
        self, session: Session, finding: dict, reply: str
    ) -> None:
        path = Path(session.fs_path) / "dissent.md"
        block = (
            f"\n## {finding.get('id')} — {finding.get('pillar')} ({finding.get('severity')})\n"
            f"**Issue**: {finding.get('issue')}\n\n"
            f"**Reviewer 建议**: {finding.get('suggest')}\n\n"
            f"**Requester 拒绝理由**: {reply}\n"
        )
        with path.open("a", encoding="utf-8") as f:
            f.write(block)

    # ── events / dedup ───────────────────────────────────
    def event_seen(self, event_id: str) -> bool:
        row = self.conn().execute(
            "SELECT 1 FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        return row is not None

    def record_event(
        self, event_id: str, *, sender_oid: str, event_type: str,
        msg_type: str, size_bytes: int, content_hash: str, summary: str,
    ) -> None:
        self.conn().execute(
            """INSERT OR IGNORE INTO events
               (event_id,sender_oid,event_type,msg_type,size_bytes,content_hash,summary,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (event_id, sender_oid, event_type, msg_type, size_bytes,
             content_hash, summary, now_iso()),
        )

    def mark_event_handled(self, event_id: str) -> None:
        self.conn().execute(
            "UPDATE events SET handled=1 WHERE event_id=?", (event_id,)
        )

    # ── tasks ────────────────────────────────────────────
    def insert_task(
        self, kind: str, payload: dict, *, requester_oid: str | None = None
    ) -> int:
        cur = self.conn().execute(
            """INSERT INTO tasks(kind,payload,requester_oid,status,scheduled_at)
               VALUES(?,?,?,?,?)""",
            (kind, json.dumps(payload, ensure_ascii=False),
             requester_oid, "pending", now_iso()),
        )
        return cur.lastrowid

    def fetch_task(self, tid: int) -> dict | None:
        row = self.conn().execute(
            "SELECT * FROM tasks WHERE id=?", (tid,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        return d

    def mark_task_running(self, tid: int) -> None:
        self.conn().execute(
            "UPDATE tasks SET status='running', picked_at=?, attempts=attempts+1 WHERE id=?",
            (now_iso(), tid),
        )

    def mark_task_done(self, tid: int) -> None:
        self.conn().execute(
            "UPDATE tasks SET status='done', finished_at=? WHERE id=?",
            (now_iso(), tid),
        )

    def mark_task_failed(self, tid: int, err: str, *, terminal: bool = True) -> None:
        new_status = "failed" if terminal else "pending"
        self.conn().execute(
            "UPDATE tasks SET status=?, last_error=?, finished_at=? WHERE id=?",
            (new_status, err[:1000], now_iso() if terminal else None, tid),
        )

    def list_pending_tasks(self) -> Iterator[tuple[int, dict, str | None]]:
        rows = self.conn().execute(
            "SELECT id,payload,requester_oid FROM tasks WHERE status='pending' "
            "ORDER BY scheduled_at"
        ).fetchall()
        for r in rows:
            yield r["id"], json.loads(r["payload"]), r["requester_oid"]

    def recover_running_tasks(self) -> int:
        """Round-2 NI1: at startup, push running tasks back to pending."""
        cur = self.conn().execute(
            "UPDATE tasks SET status='pending', last_error='worker crashed mid-run' "
            "WHERE status='running'"
        )
        return cur.rowcount

    # ── llm calls (audit) ────────────────────────────────
    def log_llm_call(
        self, *, session_id: str | None, stage: str, model: str,
        prompt_tokens: int, completion_tokens: int, reasoning_tokens: int,
        cache_hit_tokens: int, latency_ms: int, finish_reason: str,
        ok: bool, error: str | None,
    ) -> None:
        self.conn().execute(
            """INSERT INTO llm_calls
               (session_id,stage,model,prompt_tokens,completion_tokens,reasoning_tokens,
                cache_hit_tokens,latency_ms,finish_reason,ok,error,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (session_id, stage, model, prompt_tokens, completion_tokens,
             reasoning_tokens, cache_hit_tokens, latency_ms, finish_reason,
             1 if ok else 0, error, now_iso()),
        )

    # ── outbound (audit) ─────────────────────────────────
    def log_outbound(
        self, *, session_id: str | None, to_open_id: str, msg_type: str,
        content_hash: str, lark_msg_id: str | None, ok: bool, error: str | None,
    ) -> None:
        self.conn().execute(
            """INSERT INTO outbound
               (session_id,to_open_id,msg_type,content_hash,lark_msg_id,ok,error,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (session_id, to_open_id, msg_type, content_hash, lark_msg_id,
             1 if ok else 0, error, now_iso()),
        )

    def outbound_already_sent(self, session_id: str, to_open_id: str, content_hash: str) -> bool:
        row = self.conn().execute(
            "SELECT 1 FROM outbound WHERE session_id=? AND to_open_id=? AND content_hash=? AND ok=1",
            (session_id, to_open_id, content_hash),
        ).fetchone()
        return row is not None
