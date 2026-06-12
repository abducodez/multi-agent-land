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

Long-running support (ADR-0013):
  * Two clocks — wall-clock cadence is the caller's concern; sim-time is the
    `turn`.  ``step(n_ticks=N)`` advances N sim-ticks in one call, so a wall-clock
    cron ("one episode per hour") maps to ``step(n_ticks=60)``.
  * ``restore()`` resumes a persisted run from the ledger tail.
  * ``snapshot_every`` periodically checkpoints a SQLite-backed ledger.
  * Per-agent token usage is metered into the governor for budget enforcement.

The observer is decoupled: the conductor notifies it after every append but
the observer never participates in cognition.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from src import observability as obs
from src.core.events import Event, normalize_session_id
from src.core.governor import BudgetExceeded, Governor
from src.core.ledger import Ledger
from src.core.projections import StageProjection, rebuild_stage
from src.scenarios.base import Scenario

if TYPE_CHECKING:
    from src.agents.base import Agent
    from src.core.observer import Observer

logger = logging.getLogger(__name__)


class Conductor:
    def __init__(
        self,
        scenario: Scenario,
        governor: Governor | None = None,
        ledger: Ledger | None = None,
        observer: "Observer | None" = None,
        snapshot_every: int | None = None,
        snapshot_path: str | Path | None = None,
    ) -> None:
        self.scenario = scenario
        self.ledger = ledger or Ledger()
        self.governor = governor or Governor()
        self.observer = observer
        self.snapshot_every = snapshot_every
        self.snapshot_path = snapshot_path
        self.run_id = str(uuid4())
        # The browser/user session driving the current run (normalized, untrusted
        # input) — stamped onto every event this conductor appends (see _append).
        self.session_id: str | None = None
        self.turn = 0
        self._trigger_queue: deque[tuple["Agent", Event]] = deque()
        # Actors still to act in the CURRENT turn — the queue ``step_one`` drains one
        # at a time so the UI can show each agent the moment it responds, instead of
        # waiting for the whole turn (ADR-0023).  ``step()`` does not use it.
        self._pending: deque["Agent"] = deque()
        # Agents that failed to act this run, newest last — a single agent's crash
        # is isolated (the rest of the cast still acts) and recorded here for the UI
        # and tests, never swallowed silently (ADR-0023).
        self.agent_errors: list[dict[str, str]] = []

    # ── projection ────────────────────────────────────────────────────────────

    @property
    def projection(self) -> StageProjection:
        # Run-scoped: the live stage shows only the current run, even though the
        # ledger is a shared, append-only store of every run (ADR-0009).
        return rebuild_stage(self.ledger.events, self.run_id)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def _cast_map(self) -> dict[str, dict[str, str | None]]:
        """Snapshot of each agent's model binding, keyed by agent name.

        Recorded on ``run.started`` so a run is self-describing — the trace alone
        says which models played which parts (handy for sponsor-track receipts).
        Agents without a manifest (Phase-0/1 fallback) are reported as unbound.
        """
        cast: dict[str, dict[str, str | None]] = {}
        for agent in self.scenario.agents:
            name = getattr(agent, "name", agent.__class__.__name__)
            manifest = getattr(agent, "manifest", None)
            cast[name] = {
                "model_endpoint": getattr(manifest, "model_endpoint", None),
                "model_profile": getattr(manifest, "model_profile", None),
            }
        return cast

    def reset(self, seed: str, *, session_id: str | None = None) -> None:
        # NOTE: we no longer wipe the ledger — it is a shared, persistent, append-only
        # store (ADR-0009), so a reset mints a *new* run rather than destroying prior
        # ones.  Only the in-conductor transient state for the old run is cleared.
        #
        # ``session_id`` (optional) attributes the run to the browser/user that started
        # it — stamped onto ``run.started`` so the per-user Archive can list "my runs"
        # without a side table (ADR-0014: every view is a projection of the log).
        if self.observer:
            self.observer.reset()
        self._trigger_queue.clear()
        self._pending.clear()
        self.agent_errors.clear()
        self.run_id = str(uuid4())
        # Normalize at the engine boundary: the id originates client-side
        # (localStorage), so malformed/oversized values degrade to None here
        # rather than reaching the ledger or the memory index.
        self.session_id = normalize_session_id(session_id)
        self.turn = 0
        self.governor.reset()
        goal = getattr(self.scenario, "goal", "")
        scenario_name = getattr(self.scenario, "name", type(self.scenario).__name__)
        cast = self._cast_map()
        obs.set_context(run_id=self.run_id, turn=self.turn)
        obs.log("run.started", run_id=self.run_id, seed=seed, goal=goal, scenario=scenario_name)
        payload: dict = {"seed": seed, "goal": goal, "scenario": scenario_name, "cast": cast}
        if self.session_id:
            payload["session_id"] = self.session_id
        genesis_start = Event(
            run_id=self.run_id,
            turn=self.turn,
            kind="run.started",
            actor="conductor",
            payload=payload,
        )
        self._append(genesis_start)
        for event in self.scenario.genesis(self.run_id, self.turn, seed):
            self._append(event)

    def finalize(
        self,
        reason: str,
        *,
        winner: str | None = None,
        winning_model: str | None = None,
        winner_kind: str | None = None,
        winning_models: list[str] | None = None,
    ) -> Event | None:
        """Close the current run with a ``run.finished`` event.

        Idempotent-safe: if this run already has a ``run.finished`` event we return
        the existing one rather than emitting a duplicate.  ``turns`` and ``tokens``
        are read from the governor's live counters.

        Attribution (ADR-0029): ``winner`` is a cast agent name (``winner_kind:
        "agent"``) or a team label (``winner_kind: "team"``).  ``winning_model`` keeps
        its original meaning — a single cast agent's endpoint, populated only for an
        agent winner — while ``winning_models`` lists the endpoint(s) behind the
        winner (every member of a winning team).  All keys are additive.
        """
        existing = [e for e in self.ledger.events_for_run(self.run_id) if e.kind == "run.finished"]
        if existing:
            return existing[0]
        stats = self.governor.stats
        finished = Event(
            run_id=self.run_id,
            turn=self.turn,
            kind="run.finished",
            actor="conductor",
            payload={
                "reason": reason,
                "winner": winner,
                "winner_kind": winner_kind,
                "winning_model": winning_model,
                "winning_models": list(winning_models or []),
                "turns": int(stats.get("current_turn", self.turn) or self.turn),
                "tokens": int(stats.get("total_tokens", 0) or 0),
            },
        )
        obs.log(
            "run.finished",
            run_id=self.run_id,
            reason=reason,
            winner=winner,
            winner_kind=winner_kind,
            winning_model=winning_model,
            turns=finished.payload["turns"],
            tokens=finished.payload["tokens"],
        )
        return self._append(finished)

    def restore(self) -> bool:
        """Resume a persisted run: adopt the ledger's run_id and last turn.

        The ledger rehydrates its own events from disk (e.g.
        ``SQLiteLedger.from_file``); this re-points the conductor at that tail so
        the next ``step()`` continues the run rather than starting fresh.  Returns
        True when there was state to restore."""
        events = self.ledger.events
        if not events:
            return False
        last = events[-1]
        self.run_id = last.run_id
        self.turn = last.turn
        self._trigger_queue.clear()
        self.governor.reset()
        return True

    def step(self, n_ticks: int = 1) -> None:
        """Advance the simulation by *n_ticks* sim-ticks (default 1).

        With an empty ledger, the first tick performs genesis instead of acting
        (preserving the original auto-reset behaviour)."""
        for _ in range(max(1, n_ticks)):
            if not self.ledger.events_for_run(self.run_id):
                self.reset(self.scenario.default_seed)
                continue
            try:
                self._tick()
            except BudgetExceeded:
                # Close the run on the ledger before the stop propagates — a headless
                # run that hits a budget bound should still be self-describing.
                self.finalize("budget")
                raise
            self._maybe_snapshot()

    def step_one(self) -> bool:
        """Advance exactly ONE actor, opening a new turn when the queue is empty.

        This is the streaming counterpart to :meth:`step`: ``step`` runs a whole turn
        (every scheduled agent) before returning, so the UI only sees the result once
        the last mind has spoken; ``step_one`` produces a single event per call, so each
        agent appears the moment it responds.  Turn semantics are preserved — a new turn
        opens (incrementing ``turn``, checking the governor, queuing this turn's
        subscription + tick actors) only when the previous turn's queue drains, and
        subscribers an agent triggers are absorbed into the same turn (mirroring the
        ``_tick`` drain loop).

        Returns True when it produced an event (or performed genesis), False when the
        opened turn had no actors.  May raise :class:`BudgetExceeded` like ``step``."""
        if not self.ledger.events_for_run(self.run_id):
            self.reset(self.scenario.default_seed)
            return True

        try:
            if not self._pending:
                self.turn += 1
                self.governor.begin_turn(self.turn)
                self.governor.check(self.turn)
                obs.set_context(turn=self.turn)
                self._pending.extend(agent for agent, _ in self._trigger_queue)
                self._trigger_queue.clear()
                self._pending.extend(self._tick_scheduled_agents())
                if not self._pending:
                    return False

            agent = self._pending.popleft()
            self._run_agent(agent, self.projection)
        except BudgetExceeded:
            self.finalize("budget")
            raise
        # Absorb subscribers this agent's event just triggered into the current turn,
        # so a subscription cascade still resolves within the turn (as in ``_tick``).
        while self._trigger_queue:
            triggered, _ = self._trigger_queue.popleft()
            self._pending.append(triggered)
        self._maybe_snapshot()
        return True

    def inject_user_event(self, text: str, label: str | None = None) -> None:
        self.turn += 1
        payload: dict[str, str] = {"text": text}
        if label:
            payload["label"] = label
        self._append(
            Event(
                run_id=self.run_id,
                turn=self.turn,
                kind="user.injected",
                actor="visitor",
                payload=payload,
            )
        )

    # ── internal ──────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        self.turn += 1
        self.governor.begin_turn(self.turn)
        self.governor.check(self.turn)
        obs.set_context(turn=self.turn)

        projection = self.projection

        with obs.span("turn", **{"mal.turn": self.turn}):
            # ── phase 1: event-triggered (subscription) agents ────────────────
            while self._trigger_queue:
                agent, _trigger = self._trigger_queue.popleft()
                self._run_agent(agent, projection)

            # ── phase 2: tick-based scheduled agents ──────────────────────────
            for agent in self._tick_scheduled_agents():
                self._run_agent(agent, projection)

    def _run_agent(self, agent: "Agent", projection: StageProjection) -> None:
        self.governor.check(self.turn)  # before the span: a budget stop is not an agent turn
        name = getattr(agent, "name", agent.__class__.__name__)
        start = time.perf_counter()
        with obs.bind(agent=name), obs.span("agent.turn", **{"mal.agent": name, "mal.turn": self.turn}):
            try:
                event = agent.act(
                    run_id=self.run_id,
                    turn=self.turn,
                    projection=projection,
                    # Run-scoped: the ledger holds EVERY run (shared store, ADR-0026);
                    # an agent's memory/context must never recall another run's — or
                    # another user's — discussion.
                    recent_events=self.ledger.events_for_run(self.run_id),
                )
            except BudgetExceeded:
                raise  # an intentional stop from the governor — never swallow it
            except Exception as exc:  # noqa: BLE001 — one agent's crash must not silence the cast
                self._note_agent_error(agent, exc)
                return
            usage = getattr(agent, "last_usage", {})
            tokens = int(usage.get("total_tokens", 0) or 0)
            cost_usd = float(usage.get("cost_usd", 0.0) or 0.0)
            obs.add_span_attrs(**{"event.kind": event.kind, "mal.tokens": tokens, "mal.cost_usd": cost_usd})
            self.governor.record_call(tokens=tokens, cost_usd=cost_usd)
            self._append(event)
            projection.apply(event)
        obs.record_agent_turn(name, time.perf_counter() - start)

    def _note_agent_error(self, agent: "Agent", exc: Exception) -> None:
        """Record (and log) an agent's failed turn without aborting the tick.

        Resilience over silence: if one mind throws (a flaky model call, a memory
        index hiccup), the others still get their turn this round, and the failure
        is visible on ``agent_errors`` rather than crashing the whole loop."""
        name = getattr(agent, "name", agent.__class__.__name__)
        self.agent_errors.append({"turn": str(self.turn), "agent": name, "error": str(exc)})
        logger.warning("agent %s failed on turn %d: %s", name, self.turn, exc, exc_info=exc)
        obs.log("agent.error", level="warning", agent=name, turn=self.turn, error=str(exc))

    def _maybe_snapshot(self) -> None:
        if not self.snapshot_every or not self.snapshot_path:
            return
        if self.turn % self.snapshot_every != 0:
            return
        snapshot_to = getattr(self.ledger, "snapshot_to", None)
        if callable(snapshot_to):
            snapshot_to(self.snapshot_path)

    def _append(self, event: Event) -> Event:
        # Stamp the session onto the envelope at the single append chokepoint, so
        # *every* action in a run is attributable/filterable by who drove it —
        # agents and scenarios never have to know sessions exist.
        if self.session_id and event.session_id is None:
            event = event.model_copy(update={"session_id": self.session_id})
        appended = self.ledger.append(event)
        obs.log(
            "event.append", level="debug", id=appended.id, kind=appended.kind, actor=appended.actor, turn=appended.turn
        )
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
            if tick_every is not None and (tick_every == 0 or self.turn % tick_every == 0):
                result.append(agent)

        # Legacy scenario scheduling (backward-compatible)
        if legacy_agents:
            scheduled = self.scenario.schedule(self.turn)
            result.extend(a for a in scheduled if a in legacy_agents)

        return result
