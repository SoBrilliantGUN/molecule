"""RunManager: async backend-run bookkeeping + SSE event store.

Separates the HTTP contract from execution:
- ``POST /forge/runs`` only *prepares* a run (write AgentRuns row, start an asyncio task)
  and returns immediately with the run_key — the heavy LLM work runs in the background.
- ``GET /forge/runs/{run_key}/events`` replays persisted events, then tails new events
  until a terminal event (done / error), keeping the connection alive with heartbeats.

Concurrency: single-process uvicorn async loop, so an in-memory per-run_key event list
plus a small ``asyncio.Condition`` is enough — no cross-process concerns here.
Events are also persisted to ``AgentRuns.events_json`` so a subscriber can recover
after a reconnect (replay happens from the DB row if the run is no longer in memory).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from services.forge import repo
from models.agent_runs import AgentRuns
from services.forge.util import dumps, loads

logger = logging.getLogger(__name__)

TERMINAL = {"done", "error"}
HEARTBEAT_SECONDS = 15  # nginx proxy_read_timeout must be > this or SSE dies silently

# A run id → live bookkeeping (only while the backend process is alive).
_live: dict[str, "RunHandle"] = {}


class RunHandle:
    """In-memory live state for one run: task, terminal flag, event cursor."""

    def __init__(self, run_key: str, task: asyncio.Task) -> None:
        self.run_key = run_key
        self.task = task
        self.terminal = False
        self._condition = asyncio.Condition()


async def emit(run_key: str, event_type: str, data: dict) -> None:
    """Append one event for a run: persist to DB + wake any waiting /events subscribers.

    Callable from the background task; never raises (observability must not break a run).
    """
    try:
        await repo.append_events_json(run_key, dumps([{"type": event_type, "data": data}]))
    except Exception as exc:  # noqa: BLE001 - persistence must not break the run
        logger.warning("persist event %s for %s failed: %s", event_type, run_key, exc)

    handle = _live.get(run_key)
    if handle is not None:
        async with handle._condition:
            handle._condition.notify_all()
    if event_type in TERMINAL and handle is not None:
        handle.terminal = True


def resolve(run_key: str) -> RunHandle | None:
    """Return the live handle for a run, or None if not in this process."""
    return _live.get(run_key)


def is_terminal(run_key: str) -> bool:
    h = _live.get(run_key)
    return bool(h and h.terminal)


async def load_events(run_key: str, after: int) -> tuple[list[dict], int]:
    """Return persisted events with sequence > after, plus the new cursor.

    Prefers the DB row (survives in-memory loss); falls back to in-memory list.
    """
    rows = await repo.find(AgentRuns, AgentRuns.run_key == run_key, limit=1)
    if rows:
        events = loads(rows[0].get("events_json"), []) or []
    else:
        events = []
    total = len(events)
    tail = [e for i, e in enumerate(events) if i > after]
    return tail, total


async def make_sse_stream(run_key: str):
    """Async generator of SSE frames for a run: replay persisted events, then tail
    new ones until terminal, with heartbeats to keep the connection alive."""

    async def _stream():
        cursor = 0
        # 1. replay persisted history (survives reconnect)
        events, cursor = await load_events(run_key, 0)
        for e in events:
            yield _frame(e["type"], e["data"])
            if e["type"] in TERMINAL:
                return
        # 2. tail live events until terminal
        handle = resolve(run_key)
        while True:
            new_events, cursor = await load_events(run_key, cursor)
            for e in new_events:
                yield _frame(e["type"], e["data"])
                if e["type"] in TERMINAL:
                    return
            if handle is None or handle.terminal:
                # run finished and its events are persisted; no more live tail
                return
            async with handle._condition:
                try:
                    await asyncio.wait_for(handle._condition.wait(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": ping\n\n"  # keepalive so the proxy/HTTP layer does not time us out

    return _stream()


def _frame(event_type: str, data: dict) -> str:
    import json

    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---- task registration helpers ----

def start_run(run_key: str, coro: Awaitable[Any]) -> RunHandle:
    """Wrap a coroutine in an asyncio task, register it, and return its handle."""
    task = asyncio.create_task(coro)
    handle = RunHandle(run_key, task)
    _live[run_key] = handle
    task.add_done_callback(lambda _t: _live.pop(run_key, None))
    return handle


async def fetch_recent(run_key: str) -> dict | None:
    """Return the AgentRuns row dict for a run (for status polling fallback)."""
    rows = await repo.find(AgentRuns, AgentRuns.run_key == run_key, limit=1)
    return rows[0] if rows else None


async def resume_interrupted_runs() -> int:
    """Requeue runs left in ``running`` after a crash (process restart).

    Called once at app startup: any row still marked ``running`` was interrupted mid-run,
    so we re-register its task via agent.resume_forge, which continues from checkpoint_json
    if a long-run plan is present, or re-runs fresh otherwise. Returns the resumed count.
    """
    from services.forge import agent

    rows = await repo.find(AgentRuns, AgentRuns.status == "running", limit=200)
    resumed = 0
    for r in rows:
        run_key = r.get("run_key")
        if not run_key or run_key in _live:
            continue
        start_run(run_key, agent.resume_forge(run_key))
        resumed += 1
    if resumed:
        logger.info("resumed %d interrupted run(s)", resumed)
    return resumed