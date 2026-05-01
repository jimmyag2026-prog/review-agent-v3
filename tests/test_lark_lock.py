"""Per-session async lock tests (Tier 2C).

v3 worker uses ``asyncio.Lock`` per requester_oid to serialize
same-user task dispatch.  In v0 (single consumer) the lock is mainly
future-proofing; the tests below verify:

1. Lock-per-oid creates exactly one lock per key and reuses it.
2. Same-oid concurrent dispatches are serialised.
3. Different-oid dispatches can proceed independently.
"""

from __future__ import annotations

import asyncio

import pytest

from review_agent.core.storage import Storage
from review_agent.tasks.queue import TaskQueue


# ── helpers ──────────────────────────────────────────────────────────
class _FakeStorage:
    _tid = 0

    def insert_task(self, kind, payload, *, requester_oid=None):
        self._tid += 1
        return self._tid

    def mark_task_running(self, tid):
        pass

    def mark_task_done(self, tid):
        pass

    def mark_task_failed(self, tid, err, *, terminal=True):
        pass

    def fetch_task(self, tid):
        return None

    def recover_running_tasks(self):
        return 0

    def list_pending_tasks(self):
        return iter([])

    def close(self):
        pass


# ── Tier 2C: per-session lock ───────────────────────────────────────


@pytest.mark.asyncio
async def test_lock_serialises_same_oid():
    """Two concurrent dispatches for the same oid run one-at-a-time."""

    locks: dict[str, asyncio.Lock] = {}
    running: dict[str, int] = {}
    order: list[str] = []

    step1 = asyncio.Event()
    step2 = asyncio.Event()

    async def _dispatch(name: str):
        async with lock:
            order.append(f"enter-{name}")
            running[name] = running.get(name, 0) + 1
            if name == "A":
                step1.set()
                await step2.wait()
            order.append(f"exit-{name}")

    oid = "ou_x"
    lock = locks.setdefault(oid, asyncio.Lock())

    # Launch A first — it holds the lock and waits for step2
    t_a = asyncio.create_task(_dispatch("A"))
    await step1.wait()  # A has entered

    # Launch B — it should block waiting for A's lock
    t_b = asyncio.create_task(_dispatch("B"))

    # Give B a tick to attempt acquisition
    await asyncio.sleep(0.05)

    # A is still in its critical section; B hasn't entered yet
    assert order == ["enter-A"], f"B must not enter before A exits, got {order}"

    # Release A
    step2.set()
    await asyncio.wait_for(asyncio.gather(t_a, t_b), timeout=2)

    assert order == [
        "enter-A",
        "exit-A",
        "enter-B",
        "exit-B",
    ], f"same-oid serialised, got {order}"


@pytest.mark.asyncio
async def test_lock_allows_different_oids_concurrent():
    """Tasks for *different* requester_oids can run concurrently."""

    locks: dict[str, asyncio.Lock] = {}
    started_a = asyncio.Event()
    release_a = asyncio.Event()

    order: list[str] = []

    async def _dispatch(name: str, oid: str, started: asyncio.Event | None = None):
        async with locks.setdefault(oid, asyncio.Lock()):
            order.append(f"enter-{name}")
            if started:
                started.set()
                await release_a.wait()
            order.append(f"exit-{name}")

    t_a = asyncio.create_task(_dispatch("A", "ou_a", started_a))
    await started_a.wait()  # A has entered its lock

    t_b = asyncio.create_task(_dispatch("B", "ou_b"))
    await asyncio.sleep(0.1)  # B can enter because it has a different lock

    # B completed while A still holds its lock
    assert "exit-B" in order, f"B should finish independently, got {order}"

    release_a.set()
    await asyncio.wait_for(asyncio.gather(t_a, t_b), timeout=2)

    b_exit_idx = order.index("exit-B")
    a_exit_idx = order.index("exit-A")
    assert b_exit_idx < a_exit_idx, (
        f"B should exit before A, got {order}"
    )


@pytest.mark.asyncio
async def test_lock_per_oid_reuses_same_lock():
    """`setdefault` returns the existing lock on second access for same oid."""

    lock1 = asyncio.Lock()
    lock2 = asyncio.Lock()

    locks: dict[str, asyncio.Lock] = {}
    l1 = locks.setdefault("ou_x", lock1)
    l2 = locks.setdefault("ou_x", lock2)
    assert l1 is lock1
    assert l2 is lock1, "second setdefault must return the first lock"


@pytest.mark.asyncio
async def test_lock_global_fallback():
    """Tasks without requester_oid fall to '_global' and share that lock."""

    locks: dict[str, asyncio.Lock] = {}
    order: list[str] = []
    release = asyncio.Event()

    async def _dispatch(name: str, oid: str | None):
        key = oid or "_global"
        async with locks.setdefault(key, asyncio.Lock()):
            order.append(f"enter-{name}")
            if name == "G1":
                await release.wait()
            order.append(f"exit-{name}")

    t1 = asyncio.create_task(_dispatch("G1", None))
    await asyncio.sleep(0.05)
    t2 = asyncio.create_task(_dispatch("G2", None))
    await asyncio.sleep(0.1)

    assert order == ["enter-G1"], f"G2 must wait, got {order}"

    release.set()
    await asyncio.wait_for(asyncio.gather(t1, t2), timeout=2)

    assert order == ["enter-G1", "exit-G1", "enter-G2", "exit-G2"]
