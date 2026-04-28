"""Per-Requester virtual queue with single round-robin consumer.

Round-1 B5 + Round-2 NB1: queue isolation per Requester so one user's
backlog never starves another. v0 still single consumer (FIFO across
queues), so a long LLM call serializes other Requesters' next task —
that is acknowledged in PRD §16. v1 will add multi-consumer.

Round-2 NI1: at startup, recover any task that was 'running' when the
process crashed by flipping it back to 'pending'. All handlers MUST be
idempotent.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from ..core.storage import Storage


class TaskQueue:
    def __init__(self, storage: Storage):
        self.storage = storage
        self._queues: dict[str, asyncio.Queue[int]] = defaultdict(asyncio.Queue)
        self._wakeup = asyncio.Event()

    def _q(self, oid: str | None) -> asyncio.Queue[int]:
        return self._queues[oid or "_global"]

    async def enqueue(self, kind: str, payload: dict, *, requester_oid: str | None = None) -> int:
        tid = self.storage.insert_task(kind, payload, requester_oid=requester_oid)
        await self._q(requester_oid).put(tid)
        self._wakeup.set()
        return tid

    async def replay_pending(self) -> int:
        """Round-2 NI1 + replay logic."""
        recovered = self.storage.recover_running_tasks()
        n = 0
        for tid, _payload, oid in self.storage.list_pending_tasks():
            await self._q(oid).put(tid)
            n += 1
        if n:
            self._wakeup.set()
        return recovered + n

    async def next(self) -> tuple[int, dict] | None:
        while True:
            for oid, q in list(self._queues.items()):
                if not q.empty():
                    tid = q.get_nowait()
                    self.storage.mark_task_running(tid)
                    task = self.storage.fetch_task(tid)
                    return tid, task
            self._wakeup.clear()
            await self._wakeup.wait()

    def mark_done(self, tid: int) -> None:
        self.storage.mark_task_done(tid)

    def mark_failed(self, tid: int, err: str, *, terminal: bool = True) -> None:
        self.storage.mark_task_failed(tid, err, terminal=terminal)
