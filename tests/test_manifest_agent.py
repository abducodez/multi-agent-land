from __future__ import annotations

from src.agents.base import ManifestAgent
from src.core.events import Event
from src.core.manifest import AgentManifest, MemoryConfig, ScheduleConfig
from src.core.projections import StageProjection, rebuild_stage
from src.models.router import ModelRouter


def _router() -> ModelRouter:
    return ModelRouter(offline=True)


def _events(*kinds: str) -> tuple[Event, ...]:
    return tuple(
        Event(run_id="r", turn=i, kind=k, actor="scene-whisperer", payload={"text": f"line {i}"})
        for i, k in enumerate(kinds, start=1)
    )


class _Seedkeeper(ManifestAgent):
    manifest = AgentManifest(
        name="scene-whisperer",
        role="worker",
        persona="You grow the wood in one strange sentence.",
        subscribes_to=["run.started", "user.injected"],
        may_emit=["world.observed"],
        schedule=ScheduleConfig(tick_every=1),
        model_profile="tiny",
        memory=MemoryConfig(window=6),
    )


class _Reflector(ManifestAgent):
    manifest = AgentManifest(
        name="scene-whisperer",
        role="worker",
        persona="You remember and believe.",
        may_emit=["world.observed"],
        model_profile="fast",
        memory=MemoryConfig(window=6, reflection_threshold=3),
    )


class _SalienceKeeper(ManifestAgent):
    manifest = AgentManifest(
        name="scene-whisperer",
        role="worker",
        persona="You recall what matters.",
        may_emit=["world.observed"],
        model_profile="balanced",
        memory=MemoryConfig(window=4, use_salience=True, salience_top_k=3),
    )


class TestManifestAgentEmits:
    def test_emits_allowed_kind(self):
        agent = _Seedkeeper(_router())
        ev = agent.act("r", 1, StageProjection(seed="moss"), _events("run.started"))
        assert ev.kind == "world.observed"  # only allowed content kind
        assert ev.actor == "scene-whisperer"

    def test_records_token_usage(self):
        agent = _Seedkeeper(_router())
        agent.act("r", 1, StageProjection(seed="moss"), ())
        assert agent.last_usage["total_tokens"] > 0

    def test_routes_by_profile(self):
        # tiny profile -> the tiny stub variant
        agent = _Seedkeeper(router := _router())
        agent.act("r", 1, StageProjection(), ())
        assert router.for_profile("tiny").variant == "stub:tiny"


class TestManifestAgentSalience:
    def test_salience_path_runs(self):
        agent = _SalienceKeeper(_router())
        events = _events("world.observed", "world.observed", "judge.verdict")
        ev = agent.act("r", 4, rebuild_stage(events), events)
        assert ev.kind == "world.observed"


class TestManifestAgentReflection:
    def test_reflection_fires_at_threshold(self):
        agent = _Reflector(_router())
        # three globally-visible events -> tracker due at threshold 3
        events = _events("world.observed", "world.observed", "world.observed")
        ev = agent.act("r", 3, rebuild_stage(events), events)
        assert ev.kind == "agent.reflected"

    def test_no_reflection_below_threshold(self):
        agent = _Reflector(_router())
        events = _events("world.observed", "world.observed")
        ev = agent.act("r", 2, rebuild_stage(events), events)
        assert ev.kind == "world.observed"

    def test_content_kinds_exclude_reflection(self):
        agent = _Reflector(_router())
        assert "agent.reflected" not in agent._content_kinds()
        assert agent._content_kinds() == ["world.observed"]
