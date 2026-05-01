from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from ..llm.base import LLMTerminalFailure
from ..util import log
from .queue import TaskQueue

_logger = log.get(__name__)

DispatchFn = Callable[[dict], Awaitable[None]]


async def run(queue: TaskQueue, dispatch: DispatchFn, *, stop: asyncio.Event | None = None) -> None:
    _locks: dict[str, asyncio.Lock] = {}
    while True:
        if stop and stop.is_set():
            return
        item = await queue.next()
        if item is None:
            continue
        tid, task = item
        oid = task.get("requester_oid") or "_global"
        lock = _locks.setdefault(oid, asyncio.Lock())
        async with lock:
            try:
                await dispatch(task)
                queue.mark_done(tid)
            except LLMTerminalFailure as e:
                _logger.exception("task %d terminal LLM failure", tid)
                queue.mark_failed(tid, str(e), terminal=True)
            except Exception as e:
                _logger.exception("task %d crashed", tid)
                queue.mark_failed(tid, str(e), terminal=True)
