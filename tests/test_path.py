import pytest

from review_agent.util.path import (
    PathEscapeError,
    atomic_write,
    resolve_session_path,
    session_root,
)


def test_session_root(tmp_path):
    root = session_root(tmp_path, "ou_a", "ses1")
    assert str(root).endswith("users/ou_a/sessions/ses1")


def test_resolve_in_scope(tmp_path):
    root = session_root(tmp_path, "ou_a", "ses1")
    root.mkdir(parents=True)
    p = resolve_session_path(tmp_path, "ou_a", "ses1", "input/foo.md")
    assert str(p).endswith("input/foo.md")


def test_reject_dotdot(tmp_path):
    session_root(tmp_path, "ou_a", "ses1").mkdir(parents=True)
    with pytest.raises(PathEscapeError):
        resolve_session_path(tmp_path, "ou_a", "ses1", "../../other_user/secret")


def test_reject_absolute(tmp_path):
    with pytest.raises(PathEscapeError):
        resolve_session_path(tmp_path, "ou_a", "ses1", "/etc/passwd")


def test_must_exist(tmp_path):
    session_root(tmp_path, "ou_a", "ses1").mkdir(parents=True)
    with pytest.raises(FileNotFoundError):
        resolve_session_path(tmp_path, "ou_a", "ses1", "missing.md", must_exist=True)


def test_atomic_write(tmp_path):
    p = tmp_path / "deep" / "file.txt"
    atomic_write(p, "hello")
    assert p.read_text() == "hello"
    # overwrite
    atomic_write(p, "world")
    assert p.read_text() == "world"
