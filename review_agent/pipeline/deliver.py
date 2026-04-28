from __future__ import annotations

import json
from pathlib import Path

from ..core.enums import SessionStatus, Stage
from ..core.models import Session
from ..core.storage import Storage
from ..util import log
from ..util.md import text_hash
from .delivery_backends import (
    DeliveryBackend,
    DeliveryResult,
    DeliveryTarget,
    LarkDmBackend,
    LarkDocBackend,
    LocalArchiveBackend,
)

_logger = log.get(__name__)


def load_targets(
    storage: Storage,
    *,
    fs_root: str,
    requester_oid: str,
    responder_oid: str,
) -> list[DeliveryTarget]:
    cfg_path = Path(fs_root) / "delivery_targets.json"
    if cfg_path.exists():
        raw = json.loads(cfg_path.read_text())
        items = raw.get("on_close", [])
    else:
        items = [
            {"name": "responder-doc", "backend": "lark_doc",
             "open_id": responder_oid, "payload": ["summary", "final"], "role": "responder"},
            {"name": "responder-dm", "backend": "lark_dm",
             "open_id": responder_oid, "payload": ["summary"], "role": "responder"},
            {"name": "requester-dm", "backend": "lark_dm",
             "open_id": requester_oid, "payload": ["summary"], "role": "requester"},
            {"name": "archive-local", "backend": "local_path",
             "path": str(Path(fs_root) / "_archive"),
             "payload": ["summary", "summary_audit", "final", "conversation",
                         "annotations", "dissent", "verdict"]},
        ]
    out = []
    for it in items:
        out.append(DeliveryTarget(
            name=it["name"], backend=it["backend"],
            open_id=it.get("open_id", "").replace("{{RESPONDER}}", responder_oid)
                                          .replace("{{REQUESTER}}", requester_oid),
            path=it.get("path", ""), payload=list(it.get("payload", [])),
            role=it.get("role", ""),
        ))
    return out


async def run(
    *,
    storage: Storage,
    session: Session,
    backends: dict[str, DeliveryBackend],
    targets: list[DeliveryTarget],
) -> list[DeliveryResult]:
    """Fan-out delivery. Round-2 NB2: status stays 'closing' until ALL targets ok.
    Already-sent targets (matched by content_hash) are skipped on retry."""
    storage.update_session(session.id, stage=Stage.CLOSING)
    ctx: dict = {}
    results: list[DeliveryResult] = []

    # 1) doc backend first (so other DMs can include doc URL)
    for t in [x for x in targets if x.backend == "lark_doc"]:
        result = await _deliver_one(storage, session, backends, t, ctx)
        results.append(result)
        if result.ok and result.doc_url:
            ctx["doc_url"] = result.doc_url

    # 2) other backends (DM / local)
    for t in [x for x in targets if x.backend != "lark_doc"]:
        result = await _deliver_one(storage, session, backends, t, ctx)
        results.append(result)

    if all(r.ok for r in results):
        storage.update_session(session.id, status=SessionStatus.CLOSED,
                               stage=Stage.CLOSED, closed_at=_now())
    return results


async def _deliver_one(
    storage: Storage, session: Session,
    backends: dict[str, DeliveryBackend], t: DeliveryTarget, ctx: dict,
) -> DeliveryResult:
    backend = backends.get(t.backend)
    if backend is None:
        result = DeliveryResult(backend=t.backend, ok=False, detail="no such backend")
        storage.log_outbound(session_id=session.id, to_open_id=t.open_id or t.path,
                             msg_type=t.backend, content_hash="-", lark_msg_id=None,
                             ok=False, error="missing backend")
        return result

    # dedup
    body_hash = ""
    if isinstance(backend, LarkDmBackend):
        body_hash = LarkDmBackend.content_hash_for(t, session, ctx)
    elif isinstance(backend, LocalArchiveBackend):
        body_hash = text_hash(",".join(t.payload))
    elif isinstance(backend, LarkDocBackend):
        body_hash = text_hash(f"doc:{','.join(t.payload)}")
    if body_hash and t.open_id and storage.outbound_already_sent(session.id, t.open_id, body_hash):
        return DeliveryResult(backend=t.backend, ok=True, detail="dedup hit")

    try:
        result = await backend.deliver(t, session, ctx)
        storage.log_outbound(
            session_id=session.id, to_open_id=t.open_id or t.path or "-",
            msg_type=t.backend, content_hash=body_hash or "-",
            lark_msg_id=result.lark_msg_id or None, ok=result.ok, error=None if result.ok else result.detail,
        )
        return result
    except Exception as e:
        _logger.exception("delivery failed for %s", t.name)
        storage.log_outbound(
            session_id=session.id, to_open_id=t.open_id or t.path or "-",
            msg_type=t.backend, content_hash=body_hash or "-",
            lark_msg_id=None, ok=False, error=str(e)[:500],
        )
        return DeliveryResult(backend=t.backend, ok=False, detail=str(e)[:500])


def _now() -> str:
    from ..util.ids import now_iso
    return now_iso()
