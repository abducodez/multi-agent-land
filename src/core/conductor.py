from __future__ import annotations

from uuid import uuid4

from src.core.events import Event
from src.core.governor import Governor
from src.core.ledger import Ledger
from src.core.projections import StageProjection, rebuild_stage
from src.scenarios.base import Scenario


class Conductor:
    def __init__(self, scenario: Scenario, governor: Governor | None = None) -> None:
        self.scenario = scenario
        self.ledger = Ledger()
        self.governor = governor or Governor()
        self.run_id = str(uuid4())
        self.turn = 0

    @property
    def projection(self) -> StageProjection:
        return rebuild_stage(self.ledger.events)

    def reset(self, seed: str) -> None:
        self.ledger.reset()
        self.run_id = str(uuid4())
        self.turn = 0
        self.governor.__init__(  # type: ignore[misc]
            max_turns=self.governor.max_turns,
            max_calls_per_turn=self.governor.max_calls_per_turn,
            max_total_calls=self.governor.max_total_calls,
        )
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
        self.governor.begin_turn(self.turn)
        self.governor.check(self.turn)
        projection = self.projection
        for agent in self.scenario.schedule(self.turn):
            self.governor.check(self.turn)
            event = agent.act(
                run_id=self.run_id,
                turn=self.turn,
                projection=projection,
                recent_events=self.ledger.events,
            )
            self.governor.record_call()
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
