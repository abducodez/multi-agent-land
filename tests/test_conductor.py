from __future__ import annotations

import pytest

from src.core.conductor import Conductor
from src.core.events import Event
from src.models.provider import DeterministicTinyModel
from src.scenarios.thousand_token_wood import build_scenario


def _conductor() -> Conductor:
    return Conductor(scenario=build_scenario())


class TestConductorReset:
    def test_reset_clears_ledger(self):
        c = _conductor()
        c.reset("seed-a")
        c.reset("seed-b")
        kinds = {e.kind for e in c.ledger.events}
        assert "run.started" in kinds
        assert len(c.ledger.events) < 10  # not accumulating across resets

    def test_reset_writes_genesis_events(self):
        c = _conductor()
        c.reset("forest awakens")
        kinds = [e.kind for e in c.ledger.events]
        assert "run.started" in kinds
        assert "world.observed" in kinds

    def test_reset_sets_turn_to_zero(self):
        c = _conductor()
        c.step()
        c.step()
        c.reset("fresh start")
        assert c.turn == 0

    def test_reset_uses_seed_in_event(self):
        c = _conductor()
        c.reset("unique-seed-xyz")
        seed_events = [e for e in c.ledger.events if e.kind == "run.started"]
        assert seed_events[0].payload["seed"] == "unique-seed-xyz"


class TestConductorStep:
    def test_step_increments_turn(self):
        c = _conductor()
        c.reset("seed")
        initial = c.turn
        c.step()
        assert c.turn == initial + 1

    def test_step_appends_events(self):
        c = _conductor()
        c.reset("seed")
        before = len(c.ledger.events)
        c.step()
        after = len(c.ledger.events)
        assert after > before

    def test_multiple_steps_accumulate(self):
        c = _conductor()
        c.reset("seed")
        for _ in range(4):
            c.step()
        assert len(c.ledger.events) >= 5  # genesis + at least one per step

    def test_step_without_reset_auto_resets(self):
        c = _conductor()
        c.step()  # should not raise
        assert len(c.ledger.events) > 0


class TestConductorInject:
    def test_inject_appends_user_event(self):
        c = _conductor()
        c.reset("seed")
        c.inject_user_event("a silver fish falls upward")
        kinds = [e.kind for e in c.ledger.events]
        assert "user.injected" in kinds

    def test_inject_text_preserved(self):
        c = _conductor()
        c.reset("seed")
        c.inject_user_event("strange message here")
        injected = [e for e in c.ledger.events if e.kind == "user.injected"]
        assert injected[-1].payload["text"] == "strange message here"


class TestConductorProjection:
    def test_projection_reflects_latest_events(self):
        c = _conductor()
        c.reset("the wood wakes")
        proj = c.projection
        assert proj.seed == "the wood wakes" or "the wood wakes" in proj.current_scene
