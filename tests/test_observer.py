from __future__ import annotations

from src.core.events import Event
from src.core.observer import Observer, ViewDiff


def _event(kind: str, actor: str = "x", payload: dict | None = None) -> Event:
    return Event(run_id="r", turn=1, kind=kind, actor=actor, payload=payload or {"text": "hi"})  # type: ignore[arg-type]


class TestObserverConsume:
    def test_scene_change_detected(self):
        obs = Observer()
        diff = obs.consume(_event("world.observed", payload={"text": "golden fog"}))
        assert diff.scene_changed
        assert diff.new_scene == "golden fog"

    def test_no_change_for_unrelated_event(self):
        obs = Observer()
        # agent.thought doesn't affect any projection field
        diff = obs.consume(_event("agent.thought", payload={"text": "pondering"}))
        assert not diff.scene_changed

    def test_new_agent_note_detected(self):
        obs = Observer()
        diff = obs.consume(_event("agent.spoke", actor="pocket-actor", payload={"text": "I want the moon"}))
        assert any("pocket-actor" in note for note in diff.new_agent_notes)

    def test_new_judge_note_detected(self):
        obs = Observer()
        diff = obs.consume(_event("judge.verdict", payload={"text": "keep it"}))
        assert diff.new_judge_notes == ["keep it"]

    def test_new_user_artifact_detected(self):
        obs = Observer()
        diff = obs.consume(_event("user.injected", payload={"text": "a lantern whispers"}))
        assert "a lantern whispers" in diff.new_user_artifacts

    def test_callback_invoked_on_change(self):
        obs = Observer()
        received: list[ViewDiff] = []
        obs.on_change(received.append)
        obs.consume(_event("world.observed", payload={"text": "new scene"}))
        assert len(received) == 1

    def test_agent_thought_triggers_callback(self):
        obs = Observer()
        received: list[ViewDiff] = []
        obs.on_change(received.append)
        # agent.thought adds to agent_notes, so the view changes and the callback fires
        obs.consume(_event("agent.thought", payload={"text": "pondering"}))
        assert len(received) == 1
        assert received[0].new_agent_notes

    def test_multiple_callbacks(self):
        obs = Observer()
        calls_a: list[ViewDiff] = []
        calls_b: list[ViewDiff] = []
        obs.on_change(calls_a.append)
        obs.on_change(calls_b.append)
        obs.consume(_event("world.observed", payload={"text": "both see this"}))
        assert len(calls_a) == 1
        assert len(calls_b) == 1

    def test_view_accumulates(self):
        obs = Observer()
        obs.consume(_event("world.observed", payload={"text": "first scene"}))
        obs.consume(_event("world.observed", payload={"text": "second scene"}))
        assert obs.view.current_scene == "second scene"

    def test_reset_clears_view(self):
        obs = Observer()
        obs.consume(_event("world.observed", payload={"text": "something"}))
        obs.reset()
        assert "curtain" in obs.view.current_scene.lower() or obs.view.current_scene == "The curtain has not risen."

    def test_consume_batch(self):
        obs = Observer()
        events = (
            _event("world.observed", payload={"text": "scene 1"}),
            _event("agent.spoke", actor="actor", payload={"text": "line 1"}),
        )
        diffs = obs.consume_batch(events)
        assert len(diffs) == 2


class TestViewDiff:
    def test_has_changes_true(self):
        d = ViewDiff(scene_changed=True, new_scene="x")
        assert d.has_changes

    def test_has_changes_false(self):
        d = ViewDiff()
        assert not d.has_changes
