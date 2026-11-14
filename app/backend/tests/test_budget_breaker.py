from services.forge.budget import Budget, BudgetExceeded, BudgetLimits
from services.forge.breaker import CLOSED, OPEN, HALF_OPEN, CircuitBreaker


def _limits(**kw):
    defaults = dict(max_turns=400, max_seconds=3600, max_tokens=3_000_000, max_cost_cny=800, max_tool_calls=800)
    defaults.update(kw)
    return BudgetLimits(**defaults)


def test_budget_charge_and_snapshot():
    b = Budget(limits=_limits(max_tokens=1000, max_cost_cny=10))
    b.charge(100, 0.01)
    snap = b.snapshot()
    assert snap["tokens"] == 100
    assert snap["cost_cny"] == 0.01


def test_budget_soft_hint_near_limit():
    b = Budget(limits=_limits(max_tokens=100))
    assert b.soft_hint() is None
    b.charge(90, 0)
    assert b.soft_hint() is not None


def test_budget_exceeded_raises():
    b = Budget(limits=_limits(max_turns=1))
    b.add_turn()
    try:
        b.check()
    except BudgetExceeded as exc:
        assert "turns" in str(exc)
    else:
        raise AssertionError("预期 BudgetExceeded")


def test_breaker_trips_after_threshold():
    cb = CircuitBreaker(key="x", threshold=2)
    assert cb.allow(0) is True
    cb.on_failure(1)  # failures=1, still closed
    assert cb.state == CLOSED
    cb.on_failure(2)  # failures=2 -> open
    assert cb.state == OPEN
    assert cb.allow(2) is False


def test_breaker_recovers_then_succeeds():
    cb = CircuitBreaker(key="x", threshold=1, recovery_seconds=30)
    cb.on_failure(0)  # open
    assert cb.state == OPEN
    assert cb.allow(31) is True  # half-open after recovery window
    assert cb.state == HALF_OPEN
    cb.on_success()
    assert cb.state == CLOSED
