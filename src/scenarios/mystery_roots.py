"""Mystery Roots — a blackboard swarm scenario.

Three specialist agents post to a shared hypothesis board, and a judge
synthesises the best answer.  This demonstrates the same engine running
a structurally different cognitive task: convergence rather than
divergent world-growth.
"""

from __future__ import annotations

from collections.abc import Iterable

from src.agents.base import Agent
from src.core.context import ContextBuilder
from src.core.events import Event
from src.core.projections import StageProjection
from src.models.openai_compat import build_from_env
from src.models.provider import ModelProvider
from src.scenarios.base import Scenario

_ctx = ContextBuilder()

_MYSTERIES = [
    "All the clocks in the wood stopped at 3:07. No one wound them down.",
    "The bridge appeared overnight. It leads somewhere the map insists does not exist.",
    "Every morning the baker finds one extra loaf — baked perfectly but with ingredients she does not own.",
]


class ClueGatherer(Agent):
    name = "clue-gatherer"
    _persona = (
        "You are a careful Clue Gatherer. Extract exactly one new, concrete clue from the "
        "current scene that has not yet been named. State it plainly in one sentence. "
        "Start with 'Clue:'. Do not speculate."
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
        text = self.model.complete("clue-gatherer", prompt)
        return Event(run_id=run_id, turn=turn, kind="agent.thought", actor=self.name, payload={"text": text})


class HypothesisFormer(Agent):
    name = "hypothesis-former"
    _persona = (
        "You are a Hypothesis Former. Based on the clues gathered so far, propose one "
        "testable explanation in a single sentence. Start with 'Hypothesis:'. "
        "Be specific. Name a cause, not just an effect."
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
        text = self.model.complete("hypothesis-former", prompt)
        return Event(run_id=run_id, turn=turn, kind="agent.spoke", actor=self.name, payload={"text": text})


class DevilsAdvocate(Agent):
    name = "devils-advocate"
    _persona = (
        "You are the Devil's Advocate. Challenge the most recent hypothesis with one "
        "sharp counter-argument or overlooked fact. Start with 'But:'. Be brief and specific."
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
        text = self.model.complete("devils-advocate", prompt)
        return Event(run_id=run_id, turn=turn, kind="agent.thought", actor=self.name, payload={"text": text})


class MysteryJudge(Agent):
    name = "mystery-judge"
    _persona = (
        "You are the Mystery Judge. After reviewing the clues and debate, declare the "
        "most likely explanation in one confident sentence. Start with 'Verdict:'. "
        "Choose the most interesting, specific answer the evidence supports."
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
        text = self.model.complete("mystery-judge", prompt)
        return Event(run_id=run_id, turn=turn, kind="judge.verdict", actor=self.name, payload={"text": text})


class _MysteryScenario(Scenario):
    def schedule(self, turn: int) -> tuple[Agent, ...]:
        n = len(self.agents)
        if turn % 4 == 0:
            return self.agents  # full sweep including judge
        if turn % 4 == 1:
            return (self.agents[0],)  # gather clue
        if turn % 4 == 2:
            return (self.agents[1],)  # form hypothesis
        return (self.agents[2],)  # challenge it


def build_scenario() -> Scenario:
    model = build_from_env()
    return _MysteryScenario(
        name="mystery-roots",
        default_seed=_MYSTERIES[0],
        agents=(
            ClueGatherer(model),
            HypothesisFormer(model),
            DevilsAdvocate(model),
            MysteryJudge(model),
        ),
        example_seeds=_MYSTERIES,
    )
