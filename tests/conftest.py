from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# ensure src/ on path (for running pytest from src/)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from review_agent.core.enums import Role
from review_agent.core.models import User
from review_agent.core.storage import Storage


@pytest.fixture
def tmp_storage(tmp_path):
    db = tmp_path / "state.db"
    fs = tmp_path / "fs"
    s = Storage(db, fs)
    yield s
    s.close()


@pytest.fixture
def admin_user():
    return User(open_id="ou_admin", display_name="Admin", roles=[Role.ADMIN, Role.RESPONDER])


@pytest.fixture
def requester_user():
    return User(
        open_id="ou_req", display_name="Req", roles=[Role.REQUESTER],
        pairing_responder_oid="ou_admin",
    )


@pytest.fixture
def session_setup(tmp_storage, admin_user, requester_user):
    tmp_storage.upsert_user(admin_user)
    tmp_storage.upsert_user(requester_user)
    s = tmp_storage.create_session(
        requester_oid=requester_user.open_id, responder_oid=admin_user.open_id,
        admin_style="tone: direct\n", review_rules="- 4 pillars\n",
        responder_profile="# profile\n",
    )
    return tmp_storage, s, admin_user, requester_user
