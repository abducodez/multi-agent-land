from __future__ import annotations

from uuid import uuid4

from src.core.events import Event
from src.core.ledger import Ledger
from src.core.projections import StageProjection, rebuild_stage
from src.scenarios.base import Scenario


class Conductor:
    def __init__(self, scenario: Scenario) -> None:
        self.scenario = scenario
        self.ledger = Ledger()
        self.run_id = str(uuid4())
        self.turn = 0

    @property
    def projection(self) -> StageProjection:
        return rebuild_stage(self.ledger.events)

    def reset(self, seed: str) -> None:
        self.ledger.reset()
        self.run_id = str(uuid4())
        self.turn = 0
        self.ledger.append(
            Event(
                run_id=self.run_id,
                turn=self.turn,
                kind="run.started",
                actor="conductor",
                payload={"seed": seed},
            )
        )
        for event in self.scenario.genesis(self.run_id, self.turn, seed):
            self.ledger.append(event)

    def step(self) -> None:
        if not self.ledger.events:
            self.reset(self.scenario.default_seed)
            return
        self.turn += 1
        projection = self.projection
        for agent in self.scenario.schedule(self.turn):
            event = agent.act(
                run_id=self.run_id,
                turn=self.turn,
                projection=projection,
                recent_events=self.ledger.events[-10:],
            )
            self.ledger.append(event)
            projection.apply(event)

    def inject_user_event(self, text: str) -> None:
        self.turn += 1
        self.ledger.append(
            Event(
                run_id=self.run_id,
                turn=self.turn,
                kind="user.injected",
                actor="visitor",
                payload={"text": text},
            )
        )

