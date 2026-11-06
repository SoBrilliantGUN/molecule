"""Persistent circuit breakers (CLOSED -> OPEN -> HALF_OPEN) with shared-resource cascade.

- State is externalised to the `breaker_states` table so failure counts survive crashes.
- Uses wall-clock time (time.time) so persisted `opened_at` is comparable across restarts.
- Tools declare a shared resource (e.g. db / llm); failures also trip `resource:<name>`.
"""

import logging
import time
from dataclasses import dataclass

from sqlalchemy import select

from models.breaker_states import BreakerStates
from services.forge import repo

logger = logging.getLogger(__name__)

CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"


@dataclass
class CircuitBreaker:
    key: str
    threshold: int = 5
    recovery_seconds: float = 30.0
    state: str = CLOSED
    failures: int = 0
    opened_at: float = 0.0

    def allow(self, now: float) -> bool:
        if self.state == OPEN and now - self.opened_at >= self.recovery_seconds:
            self.state = HALF_OPEN
        return self.state != OPEN

    def on_success(self) -> bool:
        changed = self.state != CLOSED or self.failures > 0
        self.state, self.failures = CLOSED, 0
        return changed

    def on_failure(self, now: float) -> bool:
        self.failures += 1
        if self.state == HALF_OPEN or self.failures >= self.threshold:
            self.state, self.opened_at = OPEN, now
        return True


class BreakerRegistry:
    def __init__(self) -> None:
        self._items: dict[str, CircuitBreaker] = {}

    async def _get(self, key: str) -> CircuitBreaker:
        if key in self._items:
            return self._items[key]
        breaker = CircuitBreaker(key=key)
        try:
            async with repo.session() as s:
                row = (await s.execute(select(BreakerStates).where(BreakerStates.breaker_key == key))).scalar_one_or_none()
                if row:
                    breaker.state = row.state or CLOSED
                    breaker.failures = row.failures or 0
                    breaker.opened_at = row.opened_at or 0.0
        except Exception as exc:  # persistence must never break the agent
            logger.warning("breaker load failed for %s: %s", key, exc)
        self._items[key] = breaker
        return breaker

    async def _save(self, breaker: CircuitBreaker) -> None:
        try:
            async with repo.session() as s:
                row = (await s.execute(select(BreakerStates).where(BreakerStates.breaker_key == breaker.key))).scalar_one_or_none()
                if row is None:
                    row = BreakerStates(breaker_key=breaker.key)
                    s.add(row)
                row.state, row.failures, row.opened_at = breaker.state, breaker.failures, breaker.opened_at
                await s.commit()
        except Exception as exc:
            logger.warning("breaker save failed for %s: %s", breaker.key, exc)

    async def allow(self, keys: list[str]) -> tuple[bool, str | None]:
        now = time.time()
        for key in keys:
            if not (await self._get(key)).allow(now):
                return False, key
        return True, None

    async def record(self, keys: list[str], ok: bool) -> None:
        now = time.time()
        for key in keys:
            breaker = await self._get(key)
            changed = breaker.on_success() if ok else breaker.on_failure(now)
            if changed:
                await self._save(breaker)

    async def snapshot(self) -> list[dict]:
        async with repo.session() as s:
            rows = (await s.execute(select(BreakerStates))).scalars().all()
        now = time.time()
        return [
            {"key": r.breaker_key, "state": r.state, "failures": r.failures, "opened_at": r.opened_at, "open_for_s": round(now - (r.opened_at or now), 1) if r.state == OPEN else 0}
            for r in rows
        ]

    async def reset(self, key: str | None = None) -> None:
        for k, breaker in list(self._items.items()):
            if key is None or k == key:
                breaker.on_success()
                await self._save(breaker)


breakers = BreakerRegistry()
