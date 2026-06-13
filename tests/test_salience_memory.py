from __future__ import annotations


from src.core.events import Event
from src.core.memory import (
    ReflectionTracker,
    SalienceMemory,
    _KIND_IMPORTANCE,
)


def _event(kind: str, actor: str = "x", turn: int = 1, text: str = "hello") -> Event:
    return Event(run_id="r", turn=turn, kind=kind, actor=actor, payload={"text": text})  # type: ignore[arg-type]


class TestSalienceMemory:
    def test_returns_list(self):
        mem = SalienceMemory("x")
        visible = mem.visible((), current_turn=5, query="fog")
        assert visible == []

    def test_score_judge_verdict_higher_than_agent_spoke(self):
        mem = SalienceMemory("a")
        verdict_event = _event("judge.verdict", actor="judge", turn=1, text="keep")
        spoke_event = _event("agent.spoke", actor="a", turn=1, text="keep")
        s_verdict = mem.score(verdict_event, current_turn=2, query="keep")
        s_spoke = mem.score(spoke_event, current_turn=2, query="keep")
        assert s_verdict > s_spoke  # importance difference

    def test_recency_decay(self):
        mem = SalienceMemory("a")
        e_old = _event("world.observed", turn=1)
        e_new = _event("world.observed", turn=10)
        s_old = mem.score(e_old, current_turn=15, query="")
        s_new = mem.score(e_new, current_turn=15, query="")
        assert s_new > s_old

    def test_relevance_increases_score(self):
        mem = SalienceMemory("a")
        e_match = _event("world.observed", turn=5, text="golden spores drift upward")
        e_miss = _event("world.observed", turn=5, text="completely unrelated content")
        s_match = mem.score(e_match, current_turn=6, query="golden spores")
        s_miss = mem.score(e_miss, current_turn=6, query="golden spores")
        assert s_match > s_miss

    def test_capped_at_top_k(self):
        mem = SalienceMemory("a", top_k=3)
        events = tuple(_event("world.observed", turn=i) for i in range(10))
        visible = mem.visible(events, current_turn=10, query="")
        assert len(visible) <= 3

    def test_returns_chronological_order(self):
        mem = SalienceMemory("a", top_k=5)
        events = tuple(_event("world.observed", turn=i) for i in range(5))
        visible = mem.visible(events, current_turn=5, query="")
        turns = [e.turn for e in visible]
        assert turns == sorted(turns)

    def test_format_returns_string(self):
        mem = SalienceMemory("a")
        events = (_event("world.observed", turn=1, text="something"),)
        result = mem.format_for_prompt(events, current_turn=2, query="something")
        assert isinstance(result, str)
        assert "something" in result


class TestReflectionTracker:
    def test_not_due_at_start(self):
        tracker = ReflectionTracker("a", threshold=5)
        events = tuple(_event("world.observed", turn=i) for i in range(3))
        assert not tracker.observe(events)

    def test_due_at_threshold(self):
        tracker = ReflectionTracker("a", threshold=5)
        events = tuple(_event("world.observed", turn=i) for i in range(5))
        assert tracker.observe(events)

    def test_not_due_again_immediately(self):
        tracker = ReflectionTracker("a", threshold=5)
        events = tuple(_event("world.observed", turn=i) for i in range(5))
        tracker.observe(events)
        assert not tracker.observe(events)

    def test_due_at_next_multiple(self):
        tracker = ReflectionTracker("a", threshold=3)
        events3 = tuple(_event("world.observed", turn=i) for i in range(3))
        tracker.observe(events3)
        events6 = tuple(_event("world.observed", turn=i) for i in range(6))
        assert tracker.observe(events6)

    def test_peer_speech_does_not_advance_the_reflection_clock(self):
        # Reflection compacts "what I've been through" — its cadence keys on world beats and
        # the agent's own events, NOT how chatty the table got (peers' spoke is recallable
        # but excluded from the reflection count). Three peer lines must not trip threshold 3.
        tracker = ReflectionTracker("a", threshold=3)
        peer_chatter = tuple(_event("agent.spoke", actor="b", turn=i) for i in range(3))
        assert not tracker.observe(peer_chatter)


class TestKindImportanceTable:
    def test_user_injected_high(self):
        assert _KIND_IMPORTANCE.get("user.injected", 0) > 0.9

    def test_judge_verdict_high(self):
        assert _KIND_IMPORTANCE.get("judge.verdict", 0) > 0.8

    def test_agent_reflected_higher_than_agent_spoke(self):
        assert _KIND_IMPORTANCE.get("agent.reflected", 0) > _KIND_IMPORTANCE.get("agent.spoke", 0)
