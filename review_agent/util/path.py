"""Single-point session path scoping (round-1 B1 fix).

Every fs read/write/subprocess call inside a session must go through
``resolve_session_path()``. The helper enforces that the resolved
realpath sits inside the per-Requester session directory; otherwise it
raises ``PathEscapeError``.
"""
from __future__ import annotations

import os
from pathlib import Path


class PathEscapeError(Exception):
    """A computed session path resolved outside its session sandbox."""


def session_root(fs_root: Path | str, requester_oid: str, session_id: str) -> Path:
    return Path(fs_root) / "users" / requester_oid / "sessions" / session_id


def resolve_session_path(
    fs_root: Path | str,
    requester_oid: str,
    session_id: str,
    rel: str | os.PathLike,
    *,
    must_exist: bool = False,
) -> Path:
    if os.path.isabs(rel):
        raise PathEscapeError(f"absolute path not allowed: {rel}")
    root = session_root(fs_root, requester_oid, session_id).resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as e:
        raise PathEscapeError(f"{candidate} escapes {root}") from e
    if must_exist and not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate


def assert_subpath(parent: Path | str, child: Path | str) -> None:
    p = Path(parent).resolve()
    c = Path(child).resolve()
    try:
        c.relative_to(p)
    except ValueError as e:
        raise PathEscapeError(f"{c} escapes {p}") from e


def atomic_write(path: Path, data: str | bytes, *, mode: int = 0o640) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if isinstance(data, str):
        tmp.write_text(data, encoding="utf-8")
    else:
        tmp.write_bytes(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)
