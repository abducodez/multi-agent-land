from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Governor:
    """Rate and budget guard for the conductor loop.

    Prevents runaway inference cascades by tracking calls and tokens
    per run and per turn.  Small models are cheap but many-agent
    scenarios can still burn budget surprisingly fast.
    """

    max_turns: int = 100
    max_calls_per_turn: int = 8
    max_total_calls: int = 500

    _total_calls: int = field(default=0, init=False, repr=False)
    _calls_this_turn: int = field(default=0, init=False, repr=False)
    _current_turn: int = field(default=-1, init=False, repr=False)

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

    def record_call(self) -> None:
        self._calls_this_turn += 1
        self._total_calls += 1

    @property
    def stats(self) -> dict[str, int]:
        return {
            "total_calls": self._total_calls,
            "calls_this_turn": self._calls_this_turn,
            "current_turn": self._current_turn,
        }


class BudgetExceeded(RuntimeError):
    pass
