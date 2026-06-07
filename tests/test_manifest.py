from __future__ import annotations

import pytest

from src.core.manifest import AgentManifest, MemoryConfig, ScheduleConfig, resolve_model


class TestAgentManifest:
    def test_minimal_manifest(self):
        m = AgentManifest(name="seeker", persona="You seek.")
        assert m.name == "seeker"
        assert m.role == "worker"
        assert m.model_profile == "fast"

    def test_extra_fields_rejected(self):
        with pytest.raises(Exception):
            AgentManifest(name="x", persona="y", unknown_field="bad")  # type: ignore[call-arg]

    def test_subscriptions_default_empty(self):
        m = AgentManifest(name="a", persona="b")
        assert m.subscribes_to == []
        assert m.may_emit == []

    def test_tools_default_empty(self):
        m = AgentManifest(name="a", persona="b")
        assert m.tools == []

    def test_memory_config_defaults(self):
        m = AgentManifest(name="a", persona="b")
        assert m.memory.window == 8
        assert m.memory.use_salience is False
        assert m.memory.reflection_threshold is None

    def test_schedule_config_defaults(self):
        m = AgentManifest(name="a", persona="b")
        assert m.schedule.tick_every is None
        assert m.schedule.max_consecutive == 3

    def test_full_manifest(self):
        m = AgentManifest(
            name="seedkeeper",
            role="worker",
            persona="You observe.",
            subscribes_to=["user.injected", "run.started"],
            may_emit=["world.observed"],
            schedule=ScheduleConfig(tick_every=2),
            model_profile="tiny",
            memory=MemoryConfig(window=6, use_salience=True, salience_top_k=5),
            tools=["image-gen"],
        )
        assert m.subscribes_to == ["user.injected", "run.started"]
        assert m.may_emit == ["world.observed"]
        assert m.schedule.tick_every == 2
        assert m.model_profile == "tiny"
        assert m.memory.use_salience is True
        assert "image-gen" in m.tools


class TestResolveModel:
    def test_returns_string(self):
        result = resolve_model("fast")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_all_profiles_resolve(self):
        for profile in ("tiny", "fast", "balanced", "strong"):
            result = resolve_model(profile)  # type: ignore[arg-type]
            assert isinstance(result, str)

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("MODEL_FAST", "qwen2.5-7b")
        result = resolve_model("fast")
        assert result == "qwen2.5-7b"
