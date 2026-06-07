"""Conductor — the stage manager who raises the curtain and drives the loop.

The conductor plays two roles:

  Initiator (t=0): takes the seed, writes genesis events, configures the
  cast.  This is where the scenario is translated into running state.

  Driver (t>0): each tick it decides who acts, checks the governor, fires
  the heartbeat.  Pull-based scheduling — the conductor pulls the next unit
  of work — gives a natural throttle and a natural pause point.

Scheduling is hybrid:
  1. Subscription-based: when an event is appended, agents that declared
     that event kind in their manifest.subscribes_to are queued to react.
  2. Tick-based: agents with manifest.schedule.tick_every also fire on a
     fixed interval regardless of subscriptions.
  3. Scenario fallback: if no agent has a manifest, the scenario's legacy
     schedule() method is used (backward-compatible with Phase 0/1 scenarios).

The observer is decoupled: the conductor notifies it after every append but
the observer never participates in cognition.
"""
from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING
from uuid import uuid4

from src.core.events import Event
from src.core.governor import Governor
from src.core.ledger import Ledger
from src.core.projections import StageProjection, rebuild_stage
from src.scenarios.base import Scenario

if TYPE_CHECKING:
    from src.agents.base import Agent
    from src.core.observer import Observer


class Conductor:
    def __init__(
        self,
        scenario: Scenario,
        governor: Governor | None = None,
        ledger: Ledger | None = None,
        observer: "Observer | None" = None,
    ) -> None:
        self.scenario = scenario
        self.ledger = ledger or Ledger()
        self.governor = governor or Governor()
        self.observer = observer
        self.run_id = str(uuid4())
        self.turn = 0
        self._trigger_queue: deque[tuple["Agent", Event]] = deque()

    # ── projection ────────────────────────────────────────────────────────────

    @property
    def projection(self) -> StageProjection:
        return rebuild_stage(self.ledger.events)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def reset(self, seed: str) -> None:
        self.ledger.reset()
        if self.observer:
            self.observer.reset()
        self._trigger_queue.clear()
        self.run_id = str(uuid4())
        self.turn = 0
        self.governor.__init__(  # type: ignore[misc]
            max_turns=self.governor.max_turns,
            max_calls_per_turn=self.governor.max_calls_per_turn,
            max_total_calls=self.governor.max_total_calls,
        )
        genesis_start = Event(
            run_id=self.run_id,
            turn=self.turn,
            kind="run.started",
            actor="conductor",
            payload={"seed": seed},
        )
        self._append(genesis_start)
        for event in self.scenario.genesis(self.run_id, self.turn, seed):
            self._append(event)

    def step(self) -> None:
        if not self.ledger.events:
            self.reset(self.scenario.default_seed)
            return

        self.turn += 1
        self.governor.begin_turn(self.turn)
        self.governor.check(self.turn)

        projection = self.projection

        # ── phase 1: process any event-triggered agents ───────────────────────
        while self._trigger_queue:
            self.governor.check(self.turn)
            agent, trigger = self._trigger_queue.popleft()
            event = agent.act(
                run_id=self.run_id,
                turn=self.turn,
                projection=projection,
                recent_events=self.ledger.events,
            )
            self.governor.record_call()
            self._append(event)
            projection.apply(event)

        # ── phase 2: tick-based scheduled agents ─────────────────────────────
        for agent in self._tick_scheduled_agents():
            self.governor.check(self.turn)
            event = agent.act(
                run_id=self.run_id,
                turn=self.turn,
                projection=projection,
                recent_events=self.ledger.events,
            )
            self.governor.record_call()
            self._append(event)
            projection.apply(event)

    def inject_user_event(self, text: str) -> None:
        self.turn += 1
        self._append(
            Event(
                run_id=self.run_id,
                turn=self.turn,
                kind="user.injected",
                actor="visitor",
                payload={"text": text},
            )
        )

    # ── internal ──────────────────────────────────────────────────────────────

    def _append(self, event: Event) -> Event:
        appended = self.ledger.append(event)
        if self.observer:
            self.observer.consume(appended)
        self._notify_subscribers(appended)
        return appended

    def _notify_subscribers(self, event: Event) -> None:
        """Queue agents that subscribe to this event kind."""
        for agent in self.scenario.agents:
            manifest = getattr(agent, "manifest", None)
            if manifest and event.kind in manifest.subscribes_to:
                self._trigger_queue.append((agent, event))

    def _tick_scheduled_agents(self) -> list["Agent"]:
        """Return agents that should fire this turn based on their tick schedule.

        Falls back to the scenario's legacy schedule() method for agents
        without a manifest — preserving full backward compatibility.
        """
        manifest_agents = [a for a in self.scenario.agents if getattr(a, "manifest", None)]
        legacy_agents = [a for a in self.scenario.agents if not getattr(a, "manifest", None)]

        result: list[Agent] = []

        # Manifest-driven tick scheduling
        for agent in manifest_agents:
            tick_every = agent.manifest.schedule.tick_every
            if tick_every is not None and (
                tick_every == 0 or self.turn % tick_every == 0
            ):
                result.append(agent)

        # Legacy scenario scheduling (backward-compatible)
        if legacy_agents:
            scheduled = self.scenario.schedule(self.turn)
            result.extend(a for a in scheduled if a in legacy_agents)

        return result
