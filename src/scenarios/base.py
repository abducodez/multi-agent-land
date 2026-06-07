from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.agents.base import Agent
from src.core.events import Event


@dataclass(frozen=True)
class Scenario:
    name: str
    default_seed: str
    agents: tuple[Agent, ...]

    def genesis(self, run_id: str, turn: int, seed: str) -> Iterable[Event]:
        yield Event(
            run_id=run_id,
            turn=turn,
            kind="world.observed",
            actor=self.name,
            payload={"text": f"The first clearing forms around '{seed}'."},
        )

    def schedule(self, turn: int) -> tuple[Agent, ...]:
        if turn % 3 == 0:
            return self.agents
        if turn % 2 == 0:
            return self.agents[:2]
        return self.agents[:1] + self.agents[2:]

