"""Mystery Roots — convergent blackboard-swarm scenario.

Same engine, structurally different cognitive task: clue-gatherer, hypothesis-
former, devil's-advocate, and a strong judge converge on an answer.  Like every
scenario, its cast and rules are declarative config (``config/scenarios/
mystery-roots.yaml`` + the referenced agents); this module is the stable
``build_scenario()`` entrypoint.
"""
from __future__ import annotations

from src.core.registry import default_registry
from src.scenarios.base import Scenario

SCENARIO_NAME = "mystery-roots"


def build_scenario() -> Scenario:
    return default_registry().build_scenario(SCENARIO_NAME)
