from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.config import (
    ModelProfileConfig,
    ModelsConfig,
    ScenarioConfig,
    validate_agent,
    validate_scenario,
    validate_world,
)


class TestModelProfileConfig:
    def test_model_field_works(self):
        cfg = ModelProfileConfig(model="qwen2.5-3b-instruct")
        assert cfg.model == "qwen2.5-3b-instruct"
        assert cfg.temperature == 0.8

    def test_extra_rejected(self):
        with pytest.raises(ValidationError):
            ModelProfileConfig(model="m", bogus=1)  # type: ignore[call-arg]


class TestValidateAgent:
    def test_valid(self):
        m = validate_agent({"name": "seeker", "persona": "You seek.", "may_emit": ["world.observed"]})
        assert m.name == "seeker"

    def test_invalid_raises(self):
        with pytest.raises(ValidationError):
            validate_agent({"persona": "no name"})


class TestValidateScenario:
    def test_valid_with_goal_and_cast(self):
        s = validate_scenario(
            {"name": "w", "goal": "be strange", "default_seed": "seed", "cast": ["a", "b"]}
        )
        assert s.goal == "be strange"
        assert s.cast == ["a", "b"]


class TestValidateWorld:
    def test_coherent_world(self):
        world = validate_world(
            {
                "models": {"offline": True},
                "agents": [{"name": "a", "persona": "p", "may_emit": ["world.observed"]}],
                "scenarios": [{"name": "s", "default_seed": "seed", "cast": ["a"]}],
            }
        )
        assert isinstance(world.models, ModelsConfig)
        assert isinstance(world.scenarios[0], ScenarioConfig)

    def test_cast_referencing_undefined_agent_rejected(self):
        # The cross-check that makes UI/LLM-generated config safe to run.
        with pytest.raises(ValidationError) as exc:
            validate_world(
                {
                    "agents": [{"name": "a", "persona": "p"}],
                    "scenarios": [{"name": "s", "default_seed": "seed", "cast": ["ghost"]}],
                }
            )
        assert "undefined agents" in str(exc.value)
