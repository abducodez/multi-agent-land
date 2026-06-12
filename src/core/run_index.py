"""Run index — a per-run summary projection over the append-only ledger.

This is a *projection* in the same spirit as :func:`src.core.projections.rebuild_stage`:
a pure fold over events that yields one :class:`RunSummary` per ``run_id``.  It reads the
two engine bookend events — ``run.started`` (scenario / seed / cast / start time) and
``run.finished`` (reason / winner / token+turn totals / finish time) — and ignores the
rest.  A run that has started but not finished yields a summary with the ``finished_*``
fields left ``None`` / zero.

Two implementations, one contract:

  * :func:`index_runs` is the reference oracle — a pure function over any
    ``Iterable[Event]``, folding per ``run_id`` in first-seen order.  Tests and offline
    callers use this.
  * :func:`index_runs_from_ledger` produces the *same* list straight from a ledger's
    indexed queries (``runs()`` + ``events_for_run()``), so a hosted SQL backend never
    has to stream every event through Python to list its runs.

The cast payload mirrors the enriched ``run.started`` shape (see ``src/core/events.py``):
``{agent_name: {"model_endpoint": str | None, "model_profile": str | None}}``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterable

from pydantic import BaseModel, ConfigDict, Field

from src.core.events import Event

if TYPE_CHECKING:  # avoid a hard import cycle / SQLAlchemy dependency on the offline path
    from src.core.ledger import Ledger


class CastBinding(BaseModel):
    """The model a cast member is bound to (mirrors the ``run.started`` cast entry)."""

    model_config = ConfigDict(extra="ignore")

    model_endpoint: str | None = None
    model_profile: str | None = None


class RunSummary(BaseModel):
    """A one-line summary of a single run, folded from its bookend events."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    scenario: str = ""
    seed: str = ""
    session_id: str | None = None
    cast: dict[str, CastBinding] = Field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    reason: str | None = None
    winner: str | None = None
    winner_kind: str | None = None
    """Whether ``winner`` names a cast agent (``"agent"``) or a team label
    (``"team"``); ``None`` for runs with no competition or no winner (ADR-0029)."""
    winning_model: str | None = None
    winning_models: list[str] = Field(default_factory=list)
    """Endpoint(s) behind the winner — the agent's model, or every member of a
    winning team (``None`` entries dropped).  ``winning_model`` stays the single
    agent-winner endpoint for back-compat."""
    turns: int = 0
    tokens: int = 0


def _coerce_cast(raw: Any) -> dict[str, CastBinding]:
    if not isinstance(raw, dict):
        return {}
    cast: dict[str, CastBinding] = {}
    for name, binding in raw.items():
        if isinstance(binding, dict):
            cast[name] = CastBinding(**binding)
        else:
            cast[name] = CastBinding()
    return cast


def _apply_started(summary: RunSummary, event: Event) -> None:
    payload = event.payload
    summary.scenario = payload.get("scenario", "") or ""
    summary.seed = payload.get("seed", "") or ""
    # Attribution lives on the envelope (stamped by the conductor on every event);
    # the run.started payload copy is kept for human-readable traces.
    summary.session_id = event.session_id or payload.get("session_id") or None
    summary.cast = _coerce_cast(payload.get("cast"))
    summary.started_at = event.created_at


def _apply_finished(summary: RunSummary, event: Event) -> None:
    payload = event.payload
    summary.reason = payload.get("reason")
    summary.winner = payload.get("winner")
    summary.winner_kind = payload.get("winner_kind")
    summary.winning_model = payload.get("winning_model")
    summary.winning_models = list(payload.get("winning_models") or [])
    summary.turns = int(payload.get("turns", 0) or 0)
    summary.tokens = int(payload.get("tokens", 0) or 0)
    summary.finished_at = event.created_at


def index_runs(events: Iterable[Event]) -> list[RunSummary]:
    """Fold *events* into one :class:`RunSummary` per run, in first-seen order.

    Pure reference oracle: ``run.started`` populates scenario/seed/cast/started_at and
    ``run.finished`` populates reason/winner/winning_model/turns/tokens/finished_at.
    Runs appear in the order their *first* event is seen.  All other event kinds are
    ignored (they don't change the summary), but any event is enough to register a run.
    """
    summaries: dict[str, RunSummary] = {}
    for event in events:
        summary = summaries.get(event.run_id)
        if summary is None:
            summary = RunSummary(run_id=event.run_id)
            summaries[event.run_id] = summary
        if event.kind == "run.started":
            _apply_started(summary, event)
        elif event.kind == "run.finished":
            _apply_finished(summary, event)
    return list(summaries.values())


def index_runs_from_ledger(ledger: "Ledger") -> list[RunSummary]:
    """Build the same :class:`RunSummary` list using a ledger's indexed queries.

    Equivalent to ``index_runs(ledger.events)`` but reads run-scoped slices via
    ``ledger.runs()`` + ``ledger.events_for_run()`` so SQL backends keep the work in the
    database's indexes rather than streaming the whole log through Python.  ``index_runs``
    remains the oracle the two paths are checked against.
    """
    summaries: list[RunSummary] = []
    for run_id in ledger.runs():
        summary = RunSummary(run_id=run_id)
        for event in ledger.events_for_run(run_id):
            if event.kind == "run.started":
                _apply_started(summary, event)
            elif event.kind == "run.finished":
                _apply_finished(summary, event)
        summaries.append(summary)
    return summaries
