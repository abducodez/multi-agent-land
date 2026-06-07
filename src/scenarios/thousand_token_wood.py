from __future__ import annotations

from src.agents.tiny_wood import MischiefCritic, PocketActor, SceneWhisperer
from src.models.provider import DeterministicTinyModel
from src.scenarios.base import Scenario


def build_scenario() -> Scenario:
    model = DeterministicTinyModel()
    return Scenario(
        name="thousand-token-wood",
        default_seed="A village of stage props wakes up and argues about which fairy tale they belong to.",
        agents=(
            SceneWhisperer(model),
            MischiefCritic(model),
            PocketActor(model),
        ),
    )

