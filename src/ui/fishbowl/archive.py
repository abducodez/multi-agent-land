"""The Archive — "my past sessions" for the Lab's Load drawer.

This is a thin read layer over the W1 run-history surface (ADR-0026): it folds the
shared ledger into per-run :class:`RunSummary` rows via
:func:`src.core.run_index.index_runs_from_ledger`, then keeps only the runs that
belong to *this* browser/user (``session_id``) in *this* world (``scenario``).  No
side table is introduced — the run list is a projection of the log, in keeping with
the single-source-of-truth discipline (ADR-0014).

Loading a run hands back a :class:`ReplaySession`: a read-only view the Show scrubs
without ever generating, so a past session replays verbatim with no token spend.
"""

from __future__ import annotations

from src import observability as obs
from src.core.events import normalize_session_id
from src.core.ledger_factory import make_ledger
from src.core.registry import Registry, default_registry
from src.core.run_index import RunSummary, index_runs_from_ledger
from src.ui.fishbowl.session import ReplaySession


def _sort_key(summary: RunSummary):
    # Newest first; runs missing a start time sink to the bottom deterministically.
    started = summary.started_at
    return (started is not None, started)


def list_runs(
    scenario_name: str,
    session_id: str | None,
    *,
    registry: Registry | None = None,
    limit: int = 50,
) -> list[RunSummary]:
    """Past runs for *scenario_name* started by *session_id*, newest first.

    Returns ``[]`` when the world or the session is unknown — the drawer then shows
    its empty state rather than every user's runs.  Scoping to ``session_id`` is what
    makes "my sessions only" hold across browsers (each user has their own id).
    """
    session_id = normalize_session_id(session_id)  # same boundary rule as the write path
    if not scenario_name or not session_id:
        return []
    try:
        ledger = make_ledger()
    except Exception:  # pragma: no cover - no event store configured (defensive)
        return []
    summaries = [
        s for s in index_runs_from_ledger(ledger) if s.scenario == scenario_name and s.session_id == session_id
    ]
    summaries.sort(key=_sort_key, reverse=True)
    return summaries[:limit]


def load_replay(
    run_id: str,
    *,
    registry: Registry | None = None,
    tools=None,
) -> ReplaySession | None:
    """Build a read-only :class:`ReplaySession` for *run_id*, or ``None`` if unknown.

    The run names its own scenario on ``run.started`` (ADR-0026), so a replay is
    self-describing — we rebuild the cast/meters from that scenario and replay the
    recorded events verbatim.
    """
    if not run_id:
        return None
    try:
        ledger = make_ledger()
    except Exception:  # pragma: no cover - defensive
        return None
    events = ledger.events_for_run(run_id)
    if not events:
        return None
    started = next((e for e in events if e.kind == "run.started"), None)
    scenario_name = (started.payload.get("scenario") if started is not None else None) or ""
    if not scenario_name:
        return None
    try:
        return ReplaySession(
            run_id=run_id,
            events=events,
            scenario_name=scenario_name,
            registry=registry or default_registry(),
            tools=tools,
        )
    except Exception:  # pragma: no cover - scenario no longer in the registry, etc.
        obs.log("archive.load_failed", level="warning", run_id=run_id, scenario=scenario_name)
        return None


# ── card formatting (the phosphor list rows) ──────────────────────────────────────


def _short_id(run_id: str) -> str:
    """A stable, glanceable handle for a run — last 4 hex of its uuid."""
    tail = run_id.replace("-", "")[-4:] if run_id else "????"
    return f"#{tail}"


def _fmt_tokens(tokens: int) -> str:
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}k tok"
    return f"{tokens} tok"


def _fmt_when(summary: RunSummary) -> str:
    started = summary.started_at
    if started is None:
        return "—"
    try:
        return started.strftime("%b %-d · %H:%M")
    except ValueError:  # pragma: no cover - platforms without %-d
        return started.strftime("%b %d · %H:%M")


_REASON_LABEL = {
    "verdict": "verdict",
    "budget": "budget",
    "tick_cap": "tick-cap",
    "user_stop": "stopped",
}


def run_card_label(summary: RunSummary) -> str:
    """A compact, single-line label for a run's Load button (styled by CSS).

    Shape: ``▶ #a3f9 · Jun 12 · 14:03 · 14t · WON Fox · verdict``.  An unfinished run
    reads ``…live`` so the list distinguishes resolved runs from abandoned ones.
    """
    parts = [f"▶ {_short_id(summary.run_id)}", _fmt_when(summary)]
    if summary.turns:
        parts.append(f"{summary.turns}t")
    if summary.tokens:
        parts.append(_fmt_tokens(summary.tokens))
    if summary.winner:
        parts.append(f"WON {summary.winner}")
    if summary.reason:
        parts.append(_REASON_LABEL.get(summary.reason, summary.reason))
    else:
        parts.append("…live")
    return "  ·  ".join(parts)
