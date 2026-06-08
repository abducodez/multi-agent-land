from __future__ import annotations

import pytest

from src.agents.base import ManifestAgent
from src.core.governor import Governor
from src.core.registry import HANDLERS, Registry, default_registry, register_handler
from src.models.router import ModelRouter


class TestDefaultRegistry:
    def test_loads_shipped_agents_and_scenarios(self):
        reg = default_registry()
        assert {"scene-whisperer", "mischief-critic", "pocket-actor", "echo"} <= set(reg.agents)
        assert {"clue-gatherer", "hypothesis-former", "devils-advocate", "mystery-judge"} <= set(reg.agents)
        assert {"thousand-token-wood", "mystery-roots"} <= set(reg.scenarios)

    def test_models_profiles_loaded(self):
        reg = default_registry()
        assert set(reg.models.profiles) == {"tiny", "fast", "balanced", "strong"}
        assert reg.models.profiles["tiny"].model  # non-empty concrete model

    def test_build_scenario_yields_manifest_agents_with_profiles(self):
        reg = default_registry()
        sc = reg.build_scenario("thousand-token-wood")
        assert len(sc.agents) == 4
        assert all(isinstance(a, ManifestAgent) for a in sc.agents)
        assert sc.goal  # goal threaded from config
        profiles = {a.name: a.manifest.model_profile for a in sc.agents}
        assert profiles["pocket-actor"] == "tiny"
        assert profiles["mischief-critic"] == "balanced"

    def test_build_router_offline_without_binding(self, monkeypatch):
        # No Modal binding configured → the deterministic offline stub.
        for var in ("MODAL_WORKSPACE", "MODAL_LLM_BASE_URL", "OPENAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        reg = default_registry()
        router = reg.build_router()
        assert isinstance(router, ModelRouter)
        assert router.offline is True

    def test_governor_for_uses_config_budget(self):
        reg = default_registry()
        gov = reg.governor_for("thousand-token-wood")
        assert isinstance(gov, Governor)
        assert gov.max_turns == 60  # live-safe cap from scenario YAML

    def test_unknown_agent_raises(self):
        reg = default_registry()
        with pytest.raises(KeyError):
            reg.build_agent("nobody", reg.build_router())

    def test_unknown_scenario_raises(self):
        reg = default_registry()
        with pytest.raises(KeyError):
            reg.build_scenario("no-such-scenario")


class TestHandlerBinding:
    def test_handler_class_used_when_named(self):
        @register_handler("test-handler")
        class _Custom(ManifestAgent):
            pass

        try:
            reg = Registry.from_dir()
            # bind a known manifest to the custom handler
            manifest = reg.agents["scene-whisperer"].model_copy(update={"handler": "test-handler"})
            reg.agents["scene-whisperer"] = manifest
            agent = reg.build_agent("scene-whisperer", reg.build_router())
            assert isinstance(agent, _Custom)
            assert agent.manifest.name == "scene-whisperer"
        finally:
            HANDLERS.pop("test-handler", None)
