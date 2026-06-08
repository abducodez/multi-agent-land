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


class TestGovernorTokensAndCost:
    def test_record_call_no_args_still_works(self):
        g = Governor()
        g.begin_turn(1)
        g.record_call()  # backward-compatible: no tokens/cost
        assert g.stats["total_calls"] == 1
        assert g.stats["total_tokens"] == 0

    def test_tokens_accumulate(self):
        g = Governor()
        g.begin_turn(1)
        g.record_call(tokens=120)
        g.record_call(tokens=80)
        assert g.stats["total_tokens"] == 200

    def test_raises_on_token_cap(self):
        g = Governor(max_total_tokens=100)
        g.begin_turn(1)
        g.record_call(tokens=150)
        with pytest.raises(BudgetExceeded):
            g.check(1)

    def test_raises_on_spend_cap(self):
        g = Governor(hourly_budget_usd=0.01)
        g.begin_turn(1)
        g.record_call(cost_usd=0.05)
        with pytest.raises(BudgetExceeded):
            g.check(1)

    def test_reset_clears_counters_keeps_limits(self):
        g = Governor(max_turns=7, max_total_tokens=999)
        g.begin_turn(1)
        g.record_call(tokens=50, cost_usd=0.2)
        g.reset()
        assert g.stats["total_calls"] == 0
        assert g.stats["total_tokens"] == 0
        assert g.stats["spend_usd"] == 0.0
        assert g.max_turns == 7  # limits survive reset
        assert g.max_total_tokens == 999


class TestBudgetExceededReason:
    def test_reason_is_max_turns(self):
        g = Governor(max_turns=5)
        with pytest.raises(BudgetExceeded) as excinfo:
            g.check(6)
        assert excinfo.value.reason == "max_turns"

    def test_reason_is_max_total_calls(self):
        # Per-turn cap kept high so only the total-calls bound trips.
        g = Governor(max_total_calls=2, max_calls_per_turn=100)
        g.begin_turn(1)
        g.record_call()
        g.record_call()
        with pytest.raises(BudgetExceeded) as excinfo:
            g.check(1)
        assert excinfo.value.reason == "max_total_calls"

    def test_reason_is_max_calls_per_turn(self):
        # Total-calls cap kept high so only the per-turn bound trips.
        g = Governor(max_calls_per_turn=2, max_total_calls=100)
        g.begin_turn(1)
        g.record_call()
        g.record_call()
        with pytest.raises(BudgetExceeded) as excinfo:
            g.check(1)
        assert excinfo.value.reason == "max_calls_per_turn"

    def test_reason_is_max_total_tokens(self):
        g = Governor(max_total_tokens=100)
        g.begin_turn(1)
        g.record_call(tokens=150)
        with pytest.raises(BudgetExceeded) as excinfo:
            g.check(1)
        assert excinfo.value.reason == "max_total_tokens"

    def test_reason_is_hourly_budget_usd(self):
        g = Governor(hourly_budget_usd=0.01)
        g.begin_turn(1)
        g.record_call(cost_usd=0.05)
        with pytest.raises(BudgetExceeded) as excinfo:
            g.check(1)
        assert excinfo.value.reason == "hourly_budget_usd"

    def test_remains_runtime_error_subclass(self):
        # The sibling UI unit catches generically; type hierarchy must not change.
        assert issubclass(BudgetExceeded, RuntimeError)
        g = Governor(max_turns=1)
        with pytest.raises(RuntimeError):
            g.check(2)

    def test_message_stays_human_readable(self):
        g = Governor(max_turns=5)
        with pytest.raises(BudgetExceeded) as excinfo:
            g.check(6)
        assert "Turn cap 5 reached" in str(excinfo.value)

    def test_reason_defaults_to_none_when_constructed_directly(self):
        exc = BudgetExceeded("manual raise")
        assert exc.reason is None
        assert str(exc) == "manual raise"


class TestGovernorSnapshot:
    def test_snapshot_includes_counters_and_limits(self):
        g = Governor(max_turns=7, max_total_calls=50, max_total_tokens=999, hourly_budget_usd=1.5)
        g.begin_turn(2)
        g.record_call(tokens=40, cost_usd=0.25)
        snap = g.snapshot
        assert snap["total_calls"] == 1
        assert snap["total_tokens"] == 40
        assert snap["current_turn"] == 2
        assert snap["max_turns"] == 7
        assert snap["max_total_calls"] == 50
        assert snap["max_total_tokens"] == 999
        assert snap["hourly_budget_usd"] == 1.5

    def test_snapshot_optional_limits_default_none(self):
        g = Governor()
        snap = g.snapshot
        assert snap["max_total_tokens"] is None
        assert snap["hourly_budget_usd"] is None
