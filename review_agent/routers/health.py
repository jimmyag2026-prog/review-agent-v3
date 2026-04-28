from __future__ import annotations

from fastapi import APIRouter

from ..version import __version__

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "version": __version__}
