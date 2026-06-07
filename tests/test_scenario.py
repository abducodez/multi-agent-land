from __future__ import annotations

from src.agents.base import Agent
from src.core.events import Event
from src.core.projections import StageProjection
from src.scenarios.base import Scenario
from src.scenarios.thousand_token_wood import build_scenario


class _StubAgent(Agent):
    name = "stub"

    def act(self, run_id, turn, projection: StageProjection, recent_events) -> Event:
        return Event(run_id=run_id, turn=turn, kind="agent.spoke", actor=self.name, payload={"text": "."})


def _bare_scenario() -> Scenario:
    return Scenario(name="test-scenario", default_seed="bare seed", agents=(_StubAgent(),))


class TestScenarioGenesis:
    def test_genesis_yields_events(self):
        s = build_scenario()
        events = list(s.genesis("run-1", 0, "test seed"))
        assert len(events) > 0

    def test_genesis_events_have_correct_run_id(self):
        s = build_scenario()
        events = list(s.genesis("my-run", 0, "seed"))
        assert all(e.run_id == "my-run" for e in events)

    def test_genesis_includes_world_observed(self):
        s = build_scenario()
        events = list(s.genesis("r", 0, "mossy path"))
        kinds = [e.kind for e in events]
        assert "world.observed" in kinds

    def test_genesis_seed_appears_in_payload(self):
        s = build_scenario()
        events = list(s.genesis("r", 0, "unique-seed"))
        all_text = " ".join(str(e.payload) for e in events)
        assert "unique-seed" in all_text


class TestScenarioSchedule:
    def test_schedule_returns_agents(self):
        s = build_scenario()
        agents = s.schedule(1)
        assert len(agents) > 0

    def test_schedule_varies_by_turn(self):
        s = build_scenario()
        schedules = [tuple(a.name for a in s.schedule(t)) for t in range(1, 10)]
        unique_schedules = set(schedules)
        assert len(unique_schedules) > 1  # not all turns get the same set

    def test_every_agent_gets_scheduled_eventually(self):
        s = build_scenario()
        seen = set()
        for turn in range(1, 20):
            for agent in s.schedule(turn):
                seen.add(agent.name)
        all_agents = {a.name for a in s.agents}
        assert seen == all_agents
