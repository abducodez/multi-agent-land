from __future__ import annotations

from src.agents.base import Agent
from src.core.events import Event
from src.core.projections import StageProjection
from src.models.provider import ModelProvider


class SceneWhisperer(Agent):
    name = "scene-whisperer"

    def __init__(self, model: ModelProvider) -> None:
        self.model = model

    def act(self, run_id: str, turn: int, projection: StageProjection, recent_events: tuple[Event, ...]) -> Event:
        text = self.model.complete(
            role="scene-whisperer",
            prompt=f"Seed: {projection.seed}\nCurrent scene: {projection.current_scene}\nMake the wood stranger in one vivid sentence.",
        )
        return Event(run_id=run_id, turn=turn, kind="world.observed", actor=self.name, payload={"text": text})


class MischiefCritic(Agent):
    name = "mischief-critic"

    def __init__(self, model: ModelProvider) -> None:
        self.model = model

    def act(self, run_id: str, turn: int, projection: StageProjection, recent_events: tuple[Event, ...]) -> Event:
        text = self.model.complete(
            role="mischief-critic",
            prompt=f"Current scene: {projection.current_scene}\nJudge whether it is delightful, specific, and AI-load-bearing.",
        )
        return Event(run_id=run_id, turn=turn, kind="judge.verdict", actor=self.name, payload={"text": text})


class PocketActor(Agent):
    name = "pocket-actor"

    def __init__(self, model: ModelProvider) -> None:
        self.model = model

    def act(self, run_id: str, turn: int, projection: StageProjection, recent_events: tuple[Event, ...]) -> Event:
        text = self.model.complete(
            role="pocket-actor",
            prompt=f"Scene: {projection.current_scene}\nSpeak as a tiny character who wants something impossible.",
        )
        return Event(run_id=run_id, turn=turn, kind="agent.spoke", actor=self.name, payload={"text": text})

