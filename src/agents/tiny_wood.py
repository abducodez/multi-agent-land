from __future__ import annotations

from src.agents.base import Agent
from src.core.context import ContextBuilder
from src.core.events import Event
from src.core.projections import StageProjection
from src.models.provider import ModelProvider

_ctx = ContextBuilder()


class SceneWhisperer(Agent):
    """Grows the world: one vivid observation per turn."""

    name = "scene-whisperer"
    _persona = (
        "You are the Seedkeeper of Thousand Token Wood — ancient, patient, and "
        "delighted by small impossible things. You notice what no one else does. "
        "Your job this turn: describe how the wood has changed in one specific sentence. "
        "Do not repeat the current scene verbatim. Make it stranger or more alive."
    )

    def __init__(self, model: ModelProvider) -> None:
        self.model = model

    def act(self, run_id: str, turn: int, projection: StageProjection, recent_events: tuple[Event, ...]) -> Event:
        prompt = _ctx.build(
            agent_name=self.name,
            persona=self._persona,
            projection=projection,
            all_events=recent_events,
        )
        text = self.model.complete("scene-whisperer", prompt)
        return Event(run_id=run_id, turn=turn, kind="world.observed", actor=self.name, payload={"text": text})


class MischiefCritic(Agent):
    """Judge: one verdict on whether the scene is genuinely strange."""

    name = "mischief-critic"
    _persona = (
        "You are the Mischief Critic — a tiny, sharp-eyed judge who decides if the wood "
        "is being weird enough. You love specificity, playability, and AI-native strangeness. "
        "Your job: give a one-sentence verdict. Name one thing that works and one thing that "
        "would make it stranger. Be concise. Be demanding."
    )

    def __init__(self, model: ModelProvider) -> None:
        self.model = model

    def act(self, run_id: str, turn: int, projection: StageProjection, recent_events: tuple[Event, ...]) -> Event:
        prompt = _ctx.build(
            agent_name=self.name,
            persona=self._persona,
            projection=projection,
            all_events=recent_events,
        )
        text = self.model.complete("mischief-critic", prompt)
        return Event(run_id=run_id, turn=turn, kind="judge.verdict", actor=self.name, payload={"text": text})


class PocketActor(Agent):
    """A tiny character living in the scene who wants something impossible."""

    name = "pocket-actor"
    _persona = (
        "You are a Pocket Actor — a tiny, specific being who lives inside this exact scene "
        "and wants something that cannot exist. Speak in first person. One or two sentences. "
        "Name what you want and why it's urgent. Be absurd but sincere."
    )

    def __init__(self, model: ModelProvider) -> None:
        self.model = model

    def act(self, run_id: str, turn: int, projection: StageProjection, recent_events: tuple[Event, ...]) -> Event:
        prompt = _ctx.build(
            agent_name=self.name,
            persona=self._persona,
            projection=projection,
            all_events=recent_events,
        )
        text = self.model.complete("pocket-actor", prompt)
        return Event(run_id=run_id, turn=turn, kind="agent.spoke", actor=self.name, payload={"text": text})


class EchoAgent(Agent):
    """Transforms visitor injections through the wood's logic."""

    name = "echo"
    _persona = (
        "You are the Echo of Thousand Token Wood. When visitors drop something into the forest, "
        "you return it changed — not opposite, but transformed by the wood's rules. "
        "One sentence. Take the most recent visitor disturbance and make it stranger and more alive."
    )

    def __init__(self, model: ModelProvider) -> None:
        self.model = model

    def act(self, run_id: str, turn: int, projection: StageProjection, recent_events: tuple[Event, ...]) -> Event:
        if not projection.user_artifacts:
            text = "The wood holds its breath, waiting for a disturbance."
        else:
            prompt = _ctx.build(
                agent_name=self.name,
                persona=self._persona,
                projection=projection,
                all_events=recent_events,
            )
            text = self.model.complete("echo", prompt)
        return Event(run_id=run_id, turn=turn, kind="agent.thought", actor=self.name, payload={"text": text})
