import asyncio

import pytest

from review_agent.tasks.queue import TaskQueue


@pytest.mark.asyncio
async def test_per_oid_isolation(tmp_storage):
    q = TaskQueue(tmp_storage)
    await q.enqueue("a", {"x": 1}, requester_oid="ou_a")
    await q.enqueue("a", {"x": 2}, requester_oid="ou_b")
    await q.enqueue("a", {"x": 3}, requester_oid="ou_a")

    seen: list[tuple[str, dict]] = []
    for _ in range(3):
        tid, task = await asyncio.wait_for(q.next(), timeout=1.0)
        seen.append((task["requester_oid"], task["payload"]))
        q.mark_done(tid)

    assert {oid for oid, _ in seen} == {"ou_a", "ou_b"}
    a_payloads = [p["x"] for oid, p in seen if oid == "ou_a"]
    assert a_payloads == [1, 3]


@pytest.mark.asyncio
async def test_replay_recovers_running(tmp_storage):
    tid = tmp_storage.insert_task("a", {"y": 1}, requester_oid="ou_a")
    tmp_storage.mark_task_running(tid)
    q = TaskQueue(tmp_storage)
    n = await q.replay_pending()
    assert n >= 1
    pulled, task = await asyncio.wait_for(q.next(), timeout=1.0)
    assert task["payload"]["y"] == 1
