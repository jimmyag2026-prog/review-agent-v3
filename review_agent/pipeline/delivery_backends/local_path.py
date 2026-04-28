from __future__ import annotations

import shutil
from pathlib import Path

from ...core.models import Session
from .base import DeliveryBackend, DeliveryResult, DeliveryTarget


class LocalArchiveBackend(DeliveryBackend):
    name = "local_path"

    async def deliver(
        self,
        target: DeliveryTarget,
        session: Session,
        ctx: dict,
    ) -> DeliveryResult:
        src = Path(session.fs_path)
        dst_root = Path(target.path) if target.path else src.parent / "_archive"
        dst = dst_root / session.id
        dst.mkdir(parents=True, exist_ok=True)
        names = {
            "summary": "summary.md",
            "summary_audit": "summary_audit.md",
            "final": "final/revised.md",
            "conversation": "conversation.jsonl",
            "annotations": "annotations.jsonl",
            "dissent": "dissent.md",
            "verdict": "verdict.json",
        }
        copied: list[str] = []
        for key in target.payload:
            rel = names.get(key)
            if not rel:
                continue
            srcf = src / rel
            if srcf.exists():
                outf = dst / rel
                outf.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(srcf, outf)
                copied.append(rel)
        return DeliveryResult(backend=self.name, ok=True,
                              detail=f"archived {len(copied)} files to {dst}")
