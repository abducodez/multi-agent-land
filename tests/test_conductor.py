from __future__ import annotations


from src.core.conductor import Conductor
from src.core.events import Event
from src.core.governor import Governor
from src.core.manifest import AgentManifest, ScheduleConfig
from src.scenarios.base import Scenario
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


class _CostingAgent:
    """Minimal agent that reports a per-call cost — stands in for the live gateway."""

    manifest = AgentManifest(
        name="coster",
        persona="p",
        may_emit=["world.observed"],
        schedule=ScheduleConfig(tick_every=1),
    )

    def __init__(self) -> None:
        self.last_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost_usd": 0.002}

    def act(self, run_id, turn, projection, recent_events) -> Event:
        return Event(run_id=run_id, turn=turn, kind="world.observed", actor="coster", payload={"text": "x"})


class TestConductorCostMetering:
    def test_live_cost_reaches_governor(self):
        # On the live path the agent carries real cost on last_usage; the conductor
        # must plumb it into the Governor so hourly_budget_usd is enforceable.
        scenario = Scenario(name="s", default_seed="seed", agents=(_CostingAgent(),))
        c = Conductor(scenario=scenario, governor=Governor())
        c.reset("seed")
        c.step()
        assert c.governor.stats["spend_usd"] > 0
        assert c.governor.stats["total_tokens"] >= 15

    def test_offline_cost_stays_zero(self):
        # The deterministic stub reports no cost; spend must remain 0.
        c = _conductor()
        c.reset("seed")
        c.step()
        assert c.governor.stats["spend_usd"] == 0.0


class _ExplodingAgent:
    """An agent whose turn always raises — stands in for a flaky live model call
    or a memory-index hiccup (the live failure that silenced the whole spy cast)."""

    name = "boom"
    manifest = AgentManifest(name="boom", persona="p", may_emit=["agent.spoke"], schedule=ScheduleConfig(tick_every=1))

    def __init__(self) -> None:
        self.last_usage: dict = {}

    def act(self, run_id, turn, projection, recent_events) -> Event:
        raise RuntimeError("kaboom")


class _SpeakingAgent:
    name = "speaker"
    manifest = AgentManifest(
        name="speaker", persona="p", may_emit=["agent.spoke"], schedule=ScheduleConfig(tick_every=1)
    )

    def __init__(self) -> None:
        self.last_usage: dict = {}

    def act(self, run_id, turn, projection, recent_events) -> Event:
        return Event(run_id=run_id, turn=turn, kind="agent.spoke", actor="speaker", payload={"text": "hi"})


class TestConductorResilience:
    def test_one_agent_crash_does_not_silence_the_cast(self):
        # boom is scheduled FIRST: the old loop aborted the tick after it raised,
        # so every later agent went silent (the "only spy-cara talks" symptom).
        scenario = Scenario(name="s", default_seed="seed", agents=(_ExplodingAgent(), _SpeakingAgent()))
        c = Conductor(scenario=scenario, governor=Governor())
        c.reset("seed")
        c.step()
        spoke = [e for e in c.ledger.events if e.kind == "agent.spoke"]
        assert any(e.actor == "speaker" for e in spoke), "the rest of the cast must still act"
        assert c.agent_errors and c.agent_errors[-1]["agent"] == "boom"

    def test_budget_exceeded_still_propagates(self):
        # Resilience must not swallow the governor's intentional stop: with a
        # per-turn cap of 1, the SECOND agent trips BudgetExceeded inside
        # _run_agent — exactly the branch resilience must re-raise, not absorb.
        import pytest

        from src.core.governor import BudgetExceeded

        scenario = Scenario(name="s", default_seed="seed", agents=(_SpeakingAgent(), _SpeakingAgent()))
        c = Conductor(scenario=scenario, governor=Governor(max_calls_per_turn=1))
        c.reset("seed")
        with pytest.raises(BudgetExceeded):
            c.step()


class TestConductorStepOne:
    """``step_one`` streams a single agent per call so the UI shows each mind as it
    responds, while preserving turn semantics and per-agent error isolation."""

    def test_one_event_per_call_with_turn_rollover(self):
        scenario = Scenario(name="s", default_seed="seed", agents=(_SpeakingAgent(), _SpeakingAgent()))
        c = Conductor(scenario=scenario, governor=Governor())
        c.reset("seed")
        base = len(c.ledger.events)

        c.step_one()  # turn 1, first actor
        assert len(c.ledger.events) == base + 1
        assert c.turn == 1
        c.step_one()  # turn 1, second actor — still the same turn
        assert len(c.ledger.events) == base + 2
        assert c.turn == 1
        c.step_one()  # queue drained → a NEW turn opens
        assert len(c.ledger.events) == base + 3
        assert c.turn == 2

    def test_step_one_isolates_a_failing_agent(self):
        # boom is first: its failed call produces no event but must not block the speaker.
        scenario = Scenario(name="s", default_seed="seed", agents=(_ExplodingAgent(), _SpeakingAgent()))
        c = Conductor(scenario=scenario, governor=Governor())
        c.reset("seed")
        base = len(c.ledger.events)

        c.step_one()  # pops boom → raises internally → recorded, no event appended
        assert len(c.ledger.events) == base
        assert c.agent_errors and c.agent_errors[-1]["agent"] == "boom"
        c.step_one()  # pops the speaker → one real event
        assert len(c.ledger.events) == base + 1
        assert c.ledger.events[-1].actor == "speaker"

    def test_step_one_performs_genesis_on_empty_ledger(self):
        c = _conductor()
        assert c.step_one() is True
        kinds = {e.kind for e in c.ledger.events}
        assert "run.started" in kinds and "world.observed" in kinds
