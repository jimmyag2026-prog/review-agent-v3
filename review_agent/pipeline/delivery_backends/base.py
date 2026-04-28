from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ...core.models import Session


@dataclass
class DeliveryTarget:
    name: str
    backend: str
    open_id: str = ""
    path: str = ""
    payload: list[str] = field(default_factory=list)
    role: str = ""


@dataclass
class DeliveryResult:
    backend: str
    ok: bool
    detail: str = ""
    lark_msg_id: str = ""
    doc_url: str = ""


class DeliveryBackend(ABC):
    name: str

    @abstractmethod
    async def deliver(
        self,
        target: DeliveryTarget,
        session: Session,
        ctx: dict,
    ) -> DeliveryResult: ...
