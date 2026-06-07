from __future__ import annotations

from src.agents.tiny_wood import EchoAgent, MischiefCritic, PocketActor, SceneWhisperer
from src.models.openai_compat import build_from_env
from src.scenarios.base import Scenario

_SEEDS = [
    "A village of stage props wakes up and argues about which fairy tale they belong to.",
    "The last remaining compass has decided to point at feelings instead of north.",
    "A library where every book is the memoir of a different moon.",
    "The mushrooms have started charging admission to their bioluminescent shows.",
    "Time in this clearing runs clockwise for small things and counterclockwise for large ones.",
]


def build_scenario() -> Scenario:
    model = build_from_env()
    return Scenario(
        name="thousand-token-wood",
        default_seed=_SEEDS[0],
        agents=(
            SceneWhisperer(model),
            MischiefCritic(model),
            PocketActor(model),
            EchoAgent(model),
        ),
        example_seeds=_SEEDS,
    )
