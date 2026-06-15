from __future__ import annotations

from src.core.conductor import Conductor
from src.scenarios.mystery_roots import build_scenario


class TestMysteryRootsScenario:
    def test_build_returns_scenario(self):
        s = build_scenario()
        assert s.name == "mystery-roots"

    def test_has_five_agents(self):
        # Four investigators/judge plus the universal color commentator (an observer).
        s = build_scenario()
        assert len(s.agents) == 5

    def test_example_seeds_non_empty(self):
        s = build_scenario()
        assert len(s.example_seeds) > 0

    def test_conductor_can_run_five_turns(self):
        c = Conductor(scenario=build_scenario())
        c.reset("All the clocks stopped.")
        for _ in range(5):
            c.step()
        assert c.turn == 5
        assert len(c.ledger.events) >= 6  # genesis + steps

    def test_judge_verdict_appears(self):
        c = Conductor(scenario=build_scenario())
        c.reset("The bridge appeared overnight.")
        for _ in range(8):
            c.step()
        kinds = {e.kind for e in c.ledger.events}
        assert "judge.verdict" in kinds

    def test_schedule_cycles_through_all_agents(self):
        s = build_scenario()
        seen = set()
        for turn in range(1, 20):
            for agent in s.schedule(turn):
                seen.add(agent.name)
        all_names = {a.name for a in s.agents}
        assert seen == all_names
