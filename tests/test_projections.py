from __future__ import annotations

from src.core.events import Event
from src.core.projections import StageProjection, rebuild_stage


def _event(kind: str, actor: str = "x", payload: dict | None = None) -> Event:
    return Event(run_id="r", turn=1, kind=kind, actor=actor, payload=payload or {"text": "hi"})  # type: ignore[arg-type]


class TestStageProjectionApply:
    def test_run_started_sets_seed(self):
        proj = StageProjection()
        proj.apply(_event("run.started", payload={"seed": "test-seed"}))
        assert proj.seed == "test-seed"

    def test_run_started_updates_scene(self):
        proj = StageProjection()
        proj.apply(_event("run.started", payload={"seed": "mossy"}))
        assert "mossy" in proj.current_scene

    def test_world_observed_updates_scene(self):
        proj = StageProjection()
        proj.apply(_event("world.observed", payload={"text": "the sky hums"}))
        assert proj.current_scene == "the sky hums"

    def test_agent_spoke_appends_note(self):
        proj = StageProjection()
        proj.apply(_event("agent.spoke", actor="teller", payload={"text": "I want the moon"}))
        assert any("teller" in note for note in proj.agent_notes)

    def test_agent_notes_capped_at_eight(self):
        proj = StageProjection()
        for i in range(12):
            proj.apply(_event("agent.spoke", payload={"text": f"line {i}"}))
        assert len(proj.agent_notes) <= 8

    def test_judge_verdict_appends(self):
        proj = StageProjection()
        proj.apply(_event("judge.verdict", payload={"text": "keep it"}))
        assert len(proj.judge_notes) == 1

    def test_user_injected_appends(self):
        proj = StageProjection()
        proj.apply(_event("user.injected", payload={"text": "a lantern whispers"}))
        assert "a lantern whispers" in proj.user_artifacts

    def test_user_artifacts_capped_at_five(self):
        proj = StageProjection()
        for i in range(8):
            proj.apply(_event("user.injected", payload={"text": f"artifact {i}"}))
        assert len(proj.user_artifacts) <= 5


class TestRebuildStage:
    def test_empty_events_returns_default(self):
        proj = rebuild_stage(())
        assert "curtain" in proj.current_scene.lower() or proj.current_scene

    def test_rebuild_is_deterministic(self):
        events = (
            _event("run.started", payload={"seed": "repeat"}),
            _event("world.observed", payload={"text": "stable scene"}),
        )
        p1 = rebuild_stage(events)
        p2 = rebuild_stage(events)
        assert p1.current_scene == p2.current_scene

    def test_projection_is_pure_function_of_events(self):
        events = (_event("world.observed", payload={"text": "golden spore drift"}),)
        proj = rebuild_stage(events)
        assert proj.current_scene == "golden spore drift"
