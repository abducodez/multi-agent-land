from __future__ import annotations

from src.core.events import Event
from src.core.memory import EpisodicMemory


def _event(kind: str, actor: str = "x", turn: int = 1) -> Event:
    return Event(run_id="r", turn=turn, kind=kind, actor=actor, payload={"text": f"{actor}:{kind}"})  # type: ignore[arg-type]


class TestEpisodicMemory:
    def test_own_events_visible(self):
        mem = EpisodicMemory("seedkeeper")
        events = (_event("agent.spoke", actor="seedkeeper"),)
        visible = mem.visible(events)
        assert len(visible) == 1

    def test_world_observed_visible_to_all(self):
        mem = EpisodicMemory("pocket-actor")
        events = (_event("world.observed", actor="scene-whisperer"),)
        visible = mem.visible(events)
        assert len(visible) == 1

    def test_other_agent_spoke_not_visible(self):
        mem = EpisodicMemory("pocket-actor")
        events = (_event("agent.spoke", actor="scene-whisperer"),)
        visible = mem.visible(events)
        assert len(visible) == 0

    def test_user_injected_visible_to_all(self):
        mem = EpisodicMemory("echo")
        events = (_event("user.injected", actor="visitor"),)
        visible = mem.visible(events)
        assert len(visible) == 1

    def test_capped_at_max_recent(self):
        mem = EpisodicMemory("x", max_recent=3)
        events = tuple(_event("world.observed", turn=i) for i in range(10))
        visible = mem.visible(events)
        assert len(visible) == 3

    def test_returns_most_recent(self):
        mem = EpisodicMemory("x", max_recent=2)
        events = tuple(_event("world.observed", turn=i) for i in range(5))
        visible = mem.visible(events)
        assert visible[0].turn == 3
        assert visible[1].turn == 4

    def test_format_for_prompt_returns_string(self):
        mem = EpisodicMemory("x")
        events = (_event("world.observed", actor="narrator"),)
        result = mem.format_for_prompt(events)
        assert isinstance(result, str)

    def test_format_empty_returns_placeholder(self):
        mem = EpisodicMemory("x")
        result = mem.format_for_prompt(())
        assert "no prior" in result.lower() or result

    def test_run_started_renders_goal_not_raw_payload(self):
        # run.started carries {seed, goal} and is globally visible; the old formatter
        # dumped str(payload) — leaking the raw seed into every prompt. Now it renders
        # the shared goal only, never the seed dict.
        events = (
            Event(
                run_id="r",
                turn=0,
                kind="run.started",
                actor="conductor",
                payload={"seed": "s3cr3t-seed", "goal": "catch the spy"},  # type: ignore[arg-type]
            ),
            Event(run_id="r", turn=1, kind="agent.spoke", actor="x", payload={"text": "hello"}),  # type: ignore[arg-type]
        )
        out = EpisodicMemory("x").format_for_prompt(events)
        assert "s3cr3t-seed" not in out
        assert "catch the spy" in out
        assert "hello" in out


class _RaisingIndex:
    """A memory index whose backend is down — every call throws."""

    def index(self, events):
        raise RuntimeError("backend down")

    def search(self, query, k, run_id=None):
        raise RuntimeError("backend down")


class TestSalienceIndexResilience:
    def test_index_failure_degrades_to_keyword(self):
        # ADR-0018: the index is a derived, rebuildable lens, never load-bearing.
        # A flaky backend must degrade to keyword relevance, not crash the turn —
        # this is what kept the salience-using spy agents silent on the live path.
        from src.core.memory import SalienceMemory

        events = (Event(run_id="r", turn=1, kind="agent.spoke", actor="a", payload={"text": "warm fuel"}),)  # type: ignore[arg-type]
        mem = SalienceMemory("a", top_k=3, index=_RaisingIndex())
        out = mem.format_for_prompt(events, current_turn=1, query="warm")  # must not raise
        assert "warm fuel" in out
