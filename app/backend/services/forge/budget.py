"""Five-axis budget: turns / wall time / tokens / cost / tool calls.

Hard stop (check() before every model call) plus an 80% soft hint pinned to the input tail.
Limits are intentionally huge in v1 so we mainly collect usage data.
"""

import os
import time
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    def __init__(self, axis: str, used: float, limit: float):
        super().__init__(f"预算超限：{axis} 已用 {used:.2f} / 上限 {limit:.2f}")
        self.axis = axis


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class BudgetLimits:
    max_turns: int = int(_env_float("FORGE_MAX_TURNS", 400))
    max_seconds: float = _env_float("FORGE_MAX_SECONDS", 1800)
    max_tokens: int = int(_env_float("FORGE_MAX_TOKENS", 3_000_000))
    max_cost_cny: float = _env_float("FORGE_MAX_COST_CNY", 800)
    max_tool_calls: int = int(_env_float("FORGE_MAX_TOOL_CALLS", 800))


@dataclass
class Budget:
    limits: BudgetLimits = field(default_factory=BudgetLimits)
    started_at: float = field(default_factory=time.time)
    turns: int = 0
    tokens: int = 0
    cost_cny: float = 0.0
    tool_calls: int = 0

    def _axes(self) -> dict[str, tuple[float, float]]:
        return {
            "turns": (self.turns, self.limits.max_turns),
            "seconds": (time.time() - self.started_at, self.limits.max_seconds),
            "tokens": (self.tokens, self.limits.max_tokens),
            "cost_cny": (self.cost_cny, self.limits.max_cost_cny),
            "tool_calls": (self.tool_calls, self.limits.max_tool_calls),
        }

    def check(self) -> None:
        for axis, (used, limit) in self._axes().items():
            if used >= limit:
                raise BudgetExceeded(axis, used, limit)

    def usage_ratio(self) -> float:
        return max(used / limit for used, limit in self._axes().values() if limit > 0)

    def soft_hint(self) -> str | None:
        if self.usage_ratio() >= 0.8:
            return "【系统提示】预算已使用 80% 以上，请尽快收尾：直接给出最终结果，不要再展开新的探索。"
        return None

    def add_turn(self) -> None:
        self.turns += 1

    def add_tool_call(self) -> None:
        self.check()
        self.tool_calls += 1

    def charge(self, tokens: int, cost_cny: float) -> None:
        self.tokens += tokens
        self.cost_cny += cost_cny

    def snapshot(self) -> dict:
        axes = self._axes()
        return {
            "turns": self.turns,
            "seconds": round(axes["seconds"][0], 2),
            "tokens": self.tokens,
            "cost_cny": round(self.cost_cny, 4),
            "tool_calls": self.tool_calls,
            "ratio": round(self.usage_ratio(), 4),
            "limits": {
                "turns": self.limits.max_turns,
                "seconds": self.limits.max_seconds,
                "tokens": self.limits.max_tokens,
                "cost_cny": self.limits.max_cost_cny,
                "tool_calls": self.limits.max_tool_calls,
            },
        }
