"""SQLAlchemy-backed append-only ledger — a durable backend for the event store.

This is the same append-only ledger contract as the in-memory ``Ledger`` and the
``SQLiteLedger``, but persisted through SQLAlchemy 2.x so the *same* code drives a
hosted Postgres (Neon) event store and a local SQLite file.  It is a backend for
the ledger, not a replacement: the ``Event`` envelope and the ``Ledger`` interface
are unchanged, and the append-only log remains the single source of truth (ADR-0014).

Design decisions (mirroring ``SQLiteLedger`` so it is a drop-in substitute):
  - UNIQUE constraint on ``id`` enforces idempotency at the DB layer; a duplicate
    insert is caught and ignored, so retried workers cannot double-write.
  - A serial ``offset`` column gives a deterministic insertion order independent of
    clock skew in ``created_at`` or duplicate ``turn`` values on retry.
  - An in-memory cache mirrors the SQLite ledger: hot reads (``events``) hit the
    cache; the DB is consulted for replay (``tail``) and on open (``from_file``).
  - ``snapshot_to`` is backend-agnostic: it copies the log into a destination
    ledger (default SQLite file), so a Postgres run snapshots to a portable file
    that ``from_file`` can reopen — no vendor backup API required.
  - ``reset`` clears the current run's events (matching ``SQLiteLedger`` semantics);
    cross-run history is a separate concern.

SQLAlchemy is imported lazily inside ``__init__`` so that importing this module —
and therefore ``src.core.*`` — never requires SQLAlchemy or a database driver to be
installed.  The offline in-memory path stays import-clean.

Backend selection lives in :mod:`src.core.ledger_factory`: with ``DATABASE_URL``
set the system uses this store; otherwise it uses the in-memory ``Ledger``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.events import Event
from src.core.ledger import Ledger

if TYPE_CHECKING:  # types only — never imported at runtime on the offline path
    from sqlalchemy import Engine, MetaData, Table


def _normalise_url(url: str | Path) -> str:
    """Accept a SQLAlchemy URL or a bare filesystem path.

    A value containing ``://`` is treated as a full SQLAlchemy URL (e.g.
    ``postgresql+psycopg://…`` or ``sqlite:///abs/path.db``).  Anything else is
    treated as a SQLite file path, including ``:memory:``.
    """
    text = str(url)
    if "://" in text:
        return text
    if text == ":memory:":
        return "sqlite://"
    return f"sqlite:///{text}"


class SqlAlchemyLedger(Ledger):
    """Persistent append-only ledger backed by SQLAlchemy (Postgres or SQLite).

    Drop-in replacement for the in-memory ``Ledger`` with the same idempotency
    guarantee, plus durable storage and snapshot/restore.  Mirrors the public
    surface of ``SQLiteLedger``: ``snapshot_to``, ``from_file``, ``tail``,
    ``latest_offset`` and ``close``.
    """

    def __init__(self, url: str | Path = ":memory:") -> None:
        # Lazy import: keeps src.core.* importable without SQLAlchemy installed.
        from sqlalchemy import (
            Column,
            DateTime,
            Index,
            Integer,
            MetaData,
            String,
            Table,
            Text,
            create_engine,
        )

        self._url = _normalise_url(url)
        # pool_pre_ping keeps pooled Neon connections healthy across idle gaps;
        # it is harmless for SQLite.
        self._engine: Engine = create_engine(self._url, pool_pre_ping=True)

        self._metadata: MetaData = MetaData()
        self._events_table: Table = Table(
            "events",
            self._metadata,
            Column("offset", Integer, primary_key=True, autoincrement=True),
            Column("id", String(64), unique=True, nullable=False),
            Column("run_id", String, nullable=False, index=True),
            Column("turn", Integer, nullable=False),
            Column("kind", String, nullable=False, index=True),
            Column("actor", String, nullable=False, index=True),
            Column("payload", Text, nullable=False),
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("schema_version", Integer, nullable=False, server_default="1"),
            Column("session_id", String, nullable=True, index=True),
            # Which model produced the event (set by the agent; ADR-0028).
            Column("model_profile", String, nullable=True),
            Column("model_id", String, nullable=True, index=True),
            # Composite index for the hottest read: events of one run, by offset.
            Index("ix_events_run_offset", "run_id", "offset"),
        )
        self._metadata.create_all(self._engine)

        self._cache: list[Event] = []
        self._seen_ids: set[str] = set()
        self._load_cache()

    # ── Ledger API ────────────────────────────────────────────────────────────

    def append(self, event: Event) -> Event:
        if event.id in self._seen_ids:
            return event
        from sqlalchemy.exc import IntegrityError

        try:
            with self._engine.begin() as conn:
                conn.execute(
                    self._events_table.insert().values(
                        id=event.id,
                        run_id=event.run_id,
                        turn=event.turn,
                        kind=event.kind,
                        actor=event.actor,
                        payload=json.dumps(event.payload),
                        created_at=_aware(event.created_at),
                        schema_version=event.schema_version,
                        session_id=event.session_id,
                        model_profile=event.model_profile,
                        model_id=event.model_id,
                    )
                )
            self._cache.append(event)
            self._seen_ids.add(event.id)
        except IntegrityError:
            # Duplicate id inserted concurrently — idempotent, safe to ignore.
            self._seen_ids.add(event.id)
        return event

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._cache)

    def reset(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(self._events_table.delete())
        self._cache.clear()
        self._seen_ids.clear()

    # ── persistence helpers (parity with SQLiteLedger) ──────────────────────────

    def snapshot_to(self, dest: str | Path) -> None:
        """Copy the full log into *dest* (a SQLAlchemy URL or SQLite path).

        Backend-agnostic: unlike SQLite's native ``.backup()`` there is no portable
        vendor API across Postgres and SQLite, so the snapshot is taken by replaying
        the log into a fresh ledger at *dest*.  The result is a standalone ledger
        that ``from_file`` can reopen — typically a local SQLite file checkpointing
        a Postgres run.
        """
        target = SqlAlchemyLedger(dest)
        target.reset()
        target.extend(self._read_all())
        target.close()

    @classmethod
    def from_file(cls, path: str | Path) -> "SqlAlchemyLedger":
        """Open an existing store (URL or SQLite path) and rehydrate the cache."""
        return cls(path)

    def tail(self, from_offset: int = 0) -> tuple[Event, ...]:
        """Return events with offset > *from_offset* (for crash-recovery replay)."""
        from sqlalchemy import select

        t = self._events_table
        stmt = select(t).where(t.c.offset > from_offset).order_by(t.c.offset)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return tuple(self._row_to_event(row) for row in rows)

    def latest_offset(self) -> int:
        from sqlalchemy import func, select

        stmt = select(func.max(self._events_table.c.offset))
        with self._engine.connect() as conn:
            value = conn.execute(stmt).scalar()
        return value or 0

    def events_for_run(self, run_id: str) -> tuple[Event, ...]:
        """Return the events of *run_id* in append/offset order (indexed query)."""
        from sqlalchemy import select

        t = self._events_table
        stmt = select(t).where(t.c.run_id == run_id).order_by(t.c.offset)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return tuple(self._row_to_event(row) for row in rows)

    def runs(self) -> tuple[str, ...]:
        """Return the distinct run_ids in first-seen order (indexed query)."""
        from sqlalchemy import func, select

        t = self._events_table
        stmt = select(t.c.run_id).group_by(t.c.run_id).order_by(func.min(t.c.offset))
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).scalars().all()
        return tuple(rows)

    def close(self) -> None:
        self._engine.dispose()

    # ── internal ────────────────────────────────────────────────────────────────

    def _load_cache(self) -> None:
        for event in self._read_all():
            self._cache.append(event)
            self._seen_ids.add(event.id)

    def _read_all(self) -> list[Event]:
        from sqlalchemy import select

        t = self._events_table
        stmt = select(t).order_by(t.c.offset)
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return [self._row_to_event(row) for row in rows]

    @staticmethod
    def _row_to_event(row: Any) -> Event:
        created_at = row["created_at"]
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except (ValueError, TypeError):
                created_at = datetime.now(timezone.utc)
        created_at = _aware(created_at)
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return Event(
            id=row["id"],
            run_id=row["run_id"],
            turn=row["turn"],
            kind=row["kind"],  # type: ignore[arg-type]
            actor=row["actor"],
            payload=payload,
            created_at=created_at,
            schema_version=row["schema_version"],
            session_id=row.get("session_id"),
            model_profile=row.get("model_profile"),
            model_id=row.get("model_id"),
        )


def _aware(dt: datetime) -> datetime:
    """Coerce a datetime to timezone-aware UTC (SQLite drops tzinfo on round-trip)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
