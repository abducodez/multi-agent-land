"""The modularity invariant — the load-bearing proof of the whole architecture.

A brand-new agent and a brand-new scenario are introduced as YAML files ONLY,
loaded by the registry, and run through the conductor.  No engine file is touched.
The new agent even mints its own namespaced event kind, and it still renders on
stage via the generic projection fallback.  If this passes, "config over code" is
real, not aspirational.
"""

from __future__ import annotations

from collections import Counter

import yaml

from src.core.conductor import Conductor
from src.core.registry import Registry


def _write_world(root):
    (root / "agents").mkdir()
    (root / "scenarios").mkdir()
    (root / "models.yaml").write_text(yaml.safe_dump({"offline": True}))

    # A wholly new agent that emits a wholly new, namespaced kind.
    (root / "agents" / "town-crier.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "town-crier",
                "role": "worker",
                "persona": "You are the Town Crier. Announce one bit of news in a sentence.",
                "subscribes_to": [],
                "may_emit": ["crier.announced"],
                "schedule": {"tick_every": 1},
                "model_profile": "tiny",
                "memory": {"window": 5},
                "tools": [],
            }
        )
    )

    (root / "scenarios" / "town-square.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "town-square",
                "title": "Town Square",
                "goal": "Keep the square informed.",
                "default_seed": "Market day in a town that forgets its own name nightly.",
                "cast": ["town-crier"],
                "genesis_text": "The square fills around '{seed}'.",
            }
        )
    )


class TestModularityInvariant:
    def test_new_agent_and_scenario_run_with_zero_engine_edits(self, tmp_path):
        _write_world(tmp_path)

        registry = Registry.from_dir(tmp_path)
        assert "town-crier" in registry.agents
        assert "town-square" in registry.scenarios

        scenario = registry.build_scenario("town-square")
        conductor = Conductor(scenario, governor=registry.governor_for("town-square"))
        conductor.reset(scenario.default_seed)
        for _ in range(3):
            conductor.step()

        kinds = Counter(e.kind for e in conductor.ledger.events)
        # The brand-new namespaced kind was emitted by the brand-new agent.
        assert kinds["crier.announced"] >= 1

    def test_custom_kind_renders_on_stage(self, tmp_path):
        _write_world(tmp_path)
        registry = Registry.from_dir(tmp_path)
        conductor = Conductor(registry.build_scenario("town-square"))
        conductor.reset("Market day.")
        conductor.step()
        # Generic projection fallback surfaces any text-bearing custom kind.
        notes = " ".join(conductor.projection.agent_notes)
        assert "crier.announced" in notes or "town-crier" in notes

    def test_goal_is_threaded_from_config(self, tmp_path):
        _write_world(tmp_path)
        registry = Registry.from_dir(tmp_path)
        conductor = Conductor(registry.build_scenario("town-square"))
        conductor.reset("Market day.")
        assert conductor.projection.goal == "Keep the square informed."
