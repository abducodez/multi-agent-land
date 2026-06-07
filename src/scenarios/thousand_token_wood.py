"""Thousand Token Wood — divergent world-growth scenario.

The cast, personas, model tiers, memory, and goal now live entirely in
``config/`` (``scenarios/thousand-token-wood.yaml`` + the referenced agents).
This module is just the stable ``build_scenario()`` entrypoint that asks the
registry to assemble the declarative config into a live Scenario — proof that a
scenario is data, not code.
"""
from __future__ import annotations

from src.core.registry import default_registry
from src.scenarios.base import Scenario

SCENARIO_NAME = "thousand-token-wood"


def build_scenario() -> Scenario:
    return default_registry().build_scenario(SCENARIO_NAME)
