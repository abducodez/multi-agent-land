from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Governor:
    """Rate and budget guard for the conductor loop.

    Prevents runaway inference cascades by tracking calls, tokens, and (optional)
    spend per run and per turn.  Small models are cheap, but a 'many small models
    posting to a shared board' topology is exactly what produces surprise bills —
    so the governor is the runtime safety valve (ADR-0007, ADR-0013).

    All token/cost limits default to off, so existing call-only behaviour is
    unchanged unless a scenario opts in.
    """

    max_turns: int = 100
    max_calls_per_turn: int = 8
    max_total_calls: int = 500
    max_total_tokens: int | None = None
    hourly_budget_usd: float | None = None

    _total_calls: int = field(default=0, init=False, repr=False)
    _calls_this_turn: int = field(default=0, init=False, repr=False)
    _current_turn: int = field(default=-1, init=False, repr=False)
    _total_tokens: int = field(default=0, init=False, repr=False)
    _spend_usd: float = field(default=0.0, init=False, repr=False)

    def begin_turn(self, turn: int) -> None:
        if turn != self._current_turn:
            self._calls_this_turn = 0
            self._current_turn = turn

    def check(self, turn: int) -> None:
        if turn > self.max_turns:
            raise BudgetExceeded(f"Turn cap {self.max_turns} reached")
        if self._total_calls >= self.max_total_calls:
            raise BudgetExceeded(f"Total call cap {self.max_total_calls} reached")
        if self._calls_this_turn >= self.max_calls_per_turn:
            raise BudgetExceeded(f"Per-turn call cap {self.max_calls_per_turn} reached on turn {turn}")
        if self.max_total_tokens is not None and self._total_tokens >= self.max_total_tokens:
            raise BudgetExceeded(f"Total token cap {self.max_total_tokens} reached")
        if self.hourly_budget_usd is not None and self._spend_usd >= self.hourly_budget_usd:
            raise BudgetExceeded(f"Spend cap ${self.hourly_budget_usd:.2f} reached")

    def record_call(self, tokens: int = 0, cost_usd: float = 0.0) -> None:
        self._calls_this_turn += 1
        self._total_calls += 1
        self._total_tokens += max(0, tokens)
        self._spend_usd += max(0.0, cost_usd)

    def reset(self) -> None:
        """Zero the counters but keep the configured limits.

        Used by Conductor.reset() between runs so budget config survives a restart
        (the old code re-ran __init__, which silently dropped any extra limits)."""
        self._total_calls = 0
        self._calls_this_turn = 0
        self._current_turn = -1
        self._total_tokens = 0
        self._spend_usd = 0.0

    @property
    def stats(self) -> dict[str, int | float]:
        return {
            "total_calls": self._total_calls,
            "calls_this_turn": self._calls_this_turn,
            "current_turn": self._current_turn,
            "total_tokens": self._total_tokens,
            "spend_usd": round(self._spend_usd, 4),
        }


class BudgetExceeded(RuntimeError):
    pass
