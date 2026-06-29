"""
In-memory event bus for streaming plan generation progress to SSE clients.
One asyncio.Queue per plan_id, created before the background task starts.
"""
import asyncio
from typing import Optional

_queues: dict[str, asyncio.Queue] = {}


def create(plan_id: str) -> None:
    _queues[plan_id] = asyncio.Queue()


async def emit(plan_id: str, event: dict) -> None:
    q = _queues.get(plan_id)
    if q:
        await q.put(event)


async def read(plan_id: str, timeout: float = 30.0) -> Optional[dict]:
    """Return next event, a keepalive ping after timeout, or None if queue is gone."""
    q = _queues.get(plan_id)
    if q is None:
        return None
    try:
        return await asyncio.wait_for(q.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return {"type": "ping"}


def close(plan_id: str) -> None:
    """Signal end-of-stream with a None sentinel then remove the queue."""
    q = _queues.pop(plan_id, None)
    if q:
        q.put_nowait(None)


def exists(plan_id: str) -> bool:
    return plan_id in _queues
