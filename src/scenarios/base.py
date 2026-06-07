from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from src.agents.base import Agent
from src.core.events import Event


@dataclass(frozen=True)
class Scenario:
    name: str
    default_seed: str
    agents: tuple[Agent, ...]
    example_seeds: list[str] = field(default_factory=list)
    goal: str = ""
    """The shared objective for the cast.  Injected into every agent prompt as a
    SHARED GOAL block and carried on the genesis run.started event.  This is how a
    scenario 'sets up the goal' declaratively."""
    genesis_text: str | None = None
    """Template for the opening world.observed event.  '{seed}' is substituted.
    Falls back to a generic clearing line when None."""

    def genesis(self, run_id: str, turn: int, seed: str) -> Iterable[Event]:
        template = self.genesis_text or "The first clearing forms around '{seed}'."
        yield Event(
            run_id=run_id,
            turn=turn,
            kind="world.observed",
            actor=self.name,
            payload={"text": template.replace("{seed}", seed)},
        )

    def schedule(self, turn: int) -> tuple[Agent, ...]:
        """Legacy fallback scheduler.

        Used by the conductor only for agents WITHOUT a manifest (Phase-0/1
        compatibility).  Manifest-driven scenarios are routed by per-agent
        subscriptions + ticks instead, but this method is retained as the
        documented fallback and is exercised directly by the scenario tests.
        """
        n = len(self.agents)
        if n == 0:
            return ()
        if turn % 3 == 0:
            return self.agents
        if turn % 2 == 0:
            return self.agents[:2]
        return self.agents[:1] + (self.agents[2:3] if n > 2 else ())
