from __future__ import annotations

import pytest

from src.core.governor import BudgetExceeded, Governor


class TestGovernor:
    def test_allows_within_budget(self):
        g = Governor(max_turns=10, max_calls_per_turn=5, max_total_calls=100)
        g.begin_turn(1)
        g.check(1)  # should not raise

    def test_raises_on_turn_exceeded(self):
        g = Governor(max_turns=5)
        with pytest.raises(BudgetExceeded):
            g.check(6)

    def test_raises_on_total_calls_exceeded(self):
        g = Governor(max_total_calls=3)
        g.begin_turn(1)
        g.record_call()
        g.record_call()
        g.record_call()
        with pytest.raises(BudgetExceeded):
            g.check(1)

    def test_raises_on_per_turn_calls_exceeded(self):
        g = Governor(max_calls_per_turn=2)
        g.begin_turn(1)
        g.record_call()
        g.record_call()
        with pytest.raises(BudgetExceeded):
            g.check(1)

    def test_per_turn_resets_on_new_turn(self):
        g = Governor(max_calls_per_turn=2)
        g.begin_turn(1)
        g.record_call()
        g.record_call()
        g.begin_turn(2)  # resets per-turn count
        g.check(2)  # should not raise

    def test_stats_reflect_calls(self):
        g = Governor()
        g.begin_turn(3)
        g.record_call()
        g.record_call()
        assert g.stats["total_calls"] == 2
        assert g.stats["calls_this_turn"] == 2
        assert g.stats["current_turn"] == 3
