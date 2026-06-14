"""Leaderboard store — a dedicated, durable table for competitive run results.

Unlike :mod:`src.core.leaderboard` (which *aggregates* results), this module owns the
**persistence** of one row per decided competitive run.  It is deliberately **detached
from the event ledger**: the append-only ``events`` table stays the source of truth for
*what happened* in a run (every utterance, thought, verdict — the full logs), while this
``leaderboard_entries`` table is a separate, denormalised record of *who won* — a
materialised scoreboard the Hall of Fame reads without ever folding the event log.

Why a separate table (not a pure projection)
---------------------------------------------
A run's result is written **once**, at finish, only when it is genuinely decided — so the
Hall of Fame is a cheap ``SELECT`` over a small table rather than an O(all-events) fold of
the whole ledger on every page load.  The two tables share one database (the same
``DATABASE_URL`` / Postgres instance) but never share rows: ``events`` is the trace,
``leaderboard_entries`` is the scoreboard.  The ``run_id`` on each entry links a row back
to its full trace (``ledger.events_for_run(run_id)``) for replay.

The write gate (the operator's explicit requirement)
----------------------------------------------------
A row is recorded **only if** the run is finished AND a winner with a concrete winning
model is selected.  Unfinished runs, abandoned runs (a budget close with no verdict), and
runs whose winner has no bound model endpoint never produce a row — see
:func:`build_entry` and the call site in ``FishbowlSession.finalize``.

SQLAlchemy is imported lazily inside ``LeaderboardStore.__init__`` so importing this
module — and therefore ``src.core.*`` — never requires SQLAlchemy or a DB driver.  The
:class:`LeaderboardEntry` Pydantic model is import-clean and is reused by both the store
(persistence) and :mod:`src.core.leaderboard` (aggregation).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from src.core.run_index import CastBinding, RunSummary

if TYPE_CHECKING:  # types only — never imported at runtime on the offline path
    from sqlalchemy import Engine, MetaData, Table

    from src.scenarios.base import CompetitionConfig


# ── the row model (shared by the store and the aggregations) ─────────────────────


class LeaderboardEntry(BaseModel):
    """One decided competitive run — the durable scoreboard row.

    Carries everything the Hall of Fame needs without touching the event ledger: the
    cast→model bindings, the winner and the model(s) behind it, the competition shape
    (``kind`` / ``teams`` / ``symmetric_seats`` — needed for per-seat fairness), and the
    run's cost/timing.  ``run_id`` links the row back to its full trace for replay.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    session_id: str | None = None
    scenario: str = ""
    seed: str = ""

    # Competition shape (mirrors CompetitionConfig — needed for seat/fairness rollups).
    competition_kind: str = "none"
    teams: dict[str, list[str]] | None = None
    symmetric_seats: list[str] | None = None

    cast: dict[str, CastBinding] = Field(default_factory=dict)

    winner: str | None = None
    winner_kind: str | None = None
    winning_model: str | None = None
    winning_models: list[str] = Field(default_factory=list)

    reason: str | None = None
    turns: int = 0
    tokens: int = 0

    started_at: datetime | None = None
    finished_at: datetime | None = None
    recorded_at: datetime | None = None
    """When this scoreboard row was written (insert time)."""


def build_entry(summary: RunSummary, competition: "CompetitionConfig | None") -> LeaderboardEntry | None:
    """Build a :class:`LeaderboardEntry` from a finished run, or ``None`` if not eligible.

    The single eligibility gate (the operator's requirement): the run must be **finished**
    (``finished_at`` set), have a **winner**, carry at least one **winning model**, and be
    **competitive** (``competition.kind != "none"``).  Returns ``None`` otherwise, so the
    caller simply doesn't record a row.

    ``summary`` is the :class:`~src.core.run_index.RunSummary` folded from the run's events
    (it already resolves winner / winner_kind / winning_model(s) / cast / turns / tokens);
    ``competition`` is the scenario's config (for the seat/team shape).  This function adds
    no new attribution — it only decides eligibility and copies the fields across.
    """
    kind = getattr(competition, "kind", "none") or "none"
    winning_models = [m for m in (summary.winning_models or []) if m]
    if summary.winning_model and summary.winning_model not in winning_models:
        winning_models.append(summary.winning_model)

    finished = summary.finished_at is not None
    if not finished or not summary.winner or not winning_models or kind == "none":
        return None

    return LeaderboardEntry(
        run_id=summary.run_id,
        session_id=summary.session_id,
        scenario=summary.scenario,
        seed=summary.seed,
        competition_kind=kind,
        teams=getattr(competition, "teams", None),
        symmetric_seats=getattr(competition, "symmetric_seats", None),
        cast=dict(summary.cast),
        winner=summary.winner,
        winner_kind=summary.winner_kind,
        winning_model=summary.winning_model,
        winning_models=winning_models,
        reason=summary.reason,
        turns=summary.turns,
        tokens=summary.tokens,
        started_at=summary.started_at,
        finished_at=summary.finished_at,
    )


# ── the durable store (mirrors SqlAlchemyLedger's lazy-SQLAlchemy pattern) ────────


def _normalise_url(url: str | Path) -> str:
    """Accept a SQLAlchemy URL or a bare filesystem path (mirrors the ledger)."""
    text = str(url)
    if "://" in text:
        return text
    if text == ":memory:":
        return "sqlite://"
    return f"sqlite:///{text}"


class LeaderboardStore:
    """Durable store for :class:`LeaderboardEntry` rows in a dedicated table.

    Backed by SQLAlchemy (Postgres or SQLite), exactly like ``SqlAlchemyLedger`` — but it
    owns the ``leaderboard_entries`` table, never ``events``.  ``record`` is an idempotent
    upsert keyed on ``run_id`` (a re-finalised run — e.g. a budget close later superseded
    by a verdict — replaces its row rather than duplicating it).
    """

    def __init__(self, url: str | Path = ":memory:") -> None:
        # Lazy import: keeps src.core.* importable without SQLAlchemy installed.
        from sqlalchemy import (
            Column,
            DateTime,
            Integer,
            MetaData,
            String,
            Table,
            Text,
            create_engine,
        )

        self._url = _normalise_url(url)
        self._engine: Engine = create_engine(self._url, pool_pre_ping=True)
        self._metadata: MetaData = MetaData()
        self._table: Table = Table(
            "leaderboard_entries",
            self._metadata,
            Column("offset", Integer, primary_key=True, autoincrement=True),
            Column("run_id", String(64), unique=True, nullable=False, index=True),
            Column("session_id", String, nullable=True, index=True),
            Column("scenario", String, nullable=False, index=True),
            Column("seed", String, nullable=False, server_default=""),
            Column("competition_kind", String, nullable=False, server_default="none"),
            Column("teams", Text, nullable=True),
            Column("symmetric_seats", Text, nullable=True),
            Column("cast", Text, nullable=False, server_default="{}"),
            Column("winner", String, nullable=True),
            Column("winner_kind", String, nullable=True),
            Column("winning_model", String, nullable=True),
            Column("winning_models", Text, nullable=False, server_default="[]"),
            Column("reason", String, nullable=True),
            Column("turns", Integer, nullable=False, server_default="0"),
            Column("tokens", Integer, nullable=False, server_default="0"),
            Column("started_at", DateTime(timezone=True), nullable=True),
            Column("finished_at", DateTime(timezone=True), nullable=True),
            Column("recorded_at", DateTime(timezone=True), nullable=False),
        )
        self._metadata.create_all(self._engine)

    # ── write ──────────────────────────────────────────────────────────────────

    def record(self, entry: LeaderboardEntry) -> LeaderboardEntry:
        """Persist *entry*, upserting on ``run_id`` (idempotent — never duplicates a run).

        Stamps ``recorded_at`` if unset.  A pre-existing row for the same ``run_id`` is
        replaced (delete-then-insert in one transaction), so a corrective re-finalise
        keeps exactly one, current scoreboard row per run.
        """
        recorded = entry.model_copy(update={"recorded_at": entry.recorded_at or datetime.now(timezone.utc)})
        values = self._to_row(recorded)
        with self._engine.begin() as conn:
            conn.execute(self._table.delete().where(self._table.c.run_id == recorded.run_id))
            conn.execute(self._table.insert().values(**values))
        return recorded

    # ── read ───────────────────────────────────────────────────────────────────

    def entries(self) -> list[LeaderboardEntry]:
        """Every recorded entry, newest finish first (``run_id`` breaks ties)."""
        from sqlalchemy import select

        t = self._table
        stmt = select(t).order_by(t.c.offset)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        out = [self._row_to_entry(row) for row in rows]
        out.sort(key=lambda e: (e.finished_at is None, _neg_ts(e.finished_at), e.run_id))
        return out

    def entries_for_scenario(self, scenario: str) -> list[LeaderboardEntry]:
        """The recorded entries for one scenario (indexed query), newest finish first."""
        from sqlalchemy import select

        t = self._table
        stmt = select(t).where(t.c.scenario == scenario).order_by(t.c.offset)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        out = [self._row_to_entry(row) for row in rows]
        out.sort(key=lambda e: (e.finished_at is None, _neg_ts(e.finished_at), e.run_id))
        return out

    def close(self) -> None:
        self._engine.dispose()

    # ── internal (de)serialisation ───────────────────────────────────────────────

    @staticmethod
    def _to_row(entry: LeaderboardEntry) -> dict[str, Any]:
        return {
            "run_id": entry.run_id,
            "session_id": entry.session_id,
            "scenario": entry.scenario,
            "seed": entry.seed,
            "competition_kind": entry.competition_kind,
            "teams": json.dumps(entry.teams) if entry.teams is not None else None,
            "symmetric_seats": (json.dumps(entry.symmetric_seats) if entry.symmetric_seats is not None else None),
            "cast": json.dumps({name: binding.model_dump() for name, binding in entry.cast.items()}),
            "winner": entry.winner,
            "winner_kind": entry.winner_kind,
            "winning_model": entry.winning_model,
            "winning_models": json.dumps(list(entry.winning_models)),
            "reason": entry.reason,
            "turns": int(entry.turns),
            "tokens": int(entry.tokens),
            "started_at": _aware(entry.started_at),
            "finished_at": _aware(entry.finished_at),
            "recorded_at": _aware(entry.recorded_at) or datetime.now(timezone.utc),
        }

    @staticmethod
    def _row_to_entry(row: Any) -> LeaderboardEntry:
        cast_raw = _loads(row["cast"], {})
        cast = {name: CastBinding(**(binding or {})) for name, binding in cast_raw.items()}
        return LeaderboardEntry(
            run_id=row["run_id"],
            session_id=row.get("session_id"),
            scenario=row["scenario"],
            seed=row["seed"] or "",
            competition_kind=row["competition_kind"] or "none",
            teams=_loads(row.get("teams"), None),
            symmetric_seats=_loads(row.get("symmetric_seats"), None),
            cast=cast,
            winner=row.get("winner"),
            winner_kind=row.get("winner_kind"),
            winning_model=row.get("winning_model"),
            winning_models=_loads(row.get("winning_models"), []) or [],
            reason=row.get("reason"),
            turns=row["turns"] or 0,
            tokens=row["tokens"] or 0,
            started_at=_aware(_parse_dt(row.get("started_at"))),
            finished_at=_aware(_parse_dt(row.get("finished_at"))),
            recorded_at=_aware(_parse_dt(row.get("recorded_at"))),
        )


# ── factory (mirrors src.core.ledger_factory — same DATABASE_URL, separate table) ─

# Memoise one store per resolved URL.  This is what makes the no-key, in-memory
# (``sqlite://``) stage demo work end-to-end within a process: the write at finalize and
# the Hall-of-Fame read share the *same* engine (a fresh in-memory engine would be a
# different, empty database).  For a file/Postgres URL it just avoids re-opening engines.
_STORES: dict[str, LeaderboardStore] = {}


def make_leaderboard_store(url: str | None = None) -> LeaderboardStore:
    """Construct (or reuse) the leaderboard store for the configured database.

    Resolves the same ``DATABASE_URL`` as the event ledger (a separate *table* in the
    same database), so no extra configuration is needed.  *url* overrides it (tests pass
    ``"sqlite://"``).  Raises :class:`RuntimeError` when neither is set — like the ledger,
    the durable store is not optional.
    """
    resolved = url or os.getenv("DATABASE_URL")
    if not resolved:
        raise RuntimeError(
            "DATABASE_URL is required for the leaderboard store — it lives in the same "
            "database as the event ledger (a separate table), so set DATABASE_URL or pass "
            "an explicit url to make_leaderboard_store()."
        )
    from src.core.ledger_factory import _normalize_db_url

    key = _normalize_db_url(resolved)
    store = _STORES.get(key)
    if store is None:
        store = LeaderboardStore(key)
        _STORES[key] = store
    return store


def _reset_store_cache() -> None:
    """Drop memoised stores (test hook — production never needs this)."""
    for store in _STORES.values():
        try:
            store.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
    _STORES.clear()


# ── tiny datetime helpers (mirror the ledger's tz coercion) ──────────────────────


def _aware(dt: datetime | None) -> datetime | None:
    """Coerce a datetime to tz-aware UTC; pass ``None`` through (SQLite drops tzinfo)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):  # pragma: no cover - defensive
        return None


def _neg_ts(value: datetime | None) -> float:
    """Negated POSIX timestamp for descending sort; ``0.0`` for ``None`` (sorted last)."""
    return -value.timestamp() if value is not None else 0.0


def _loads(value: Any, default: Any) -> Any:
    """JSON-decode a stored text column, tolerating already-decoded values / nulls."""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):  # pragma: no cover - defensive
        return default


__all__ = [
    "LeaderboardEntry",
    "LeaderboardStore",
    "build_entry",
    "make_leaderboard_store",
]
