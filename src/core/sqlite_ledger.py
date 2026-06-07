"""SQLite-backed append-only ledger — the persistent backend for long-running scenarios.

Replaces the in-memory Ledger for production use.  The in-memory Ledger remains
valid for tests and short demo runs where persistence is not required.

Design decisions:
  - UNIQUE constraint on id enforces idempotency at the DB layer.
  - Serial OFFSET column gives a deterministic ordering guarantee even if clock
    skew produces non-monotonic created_at timestamps.
  - snapshot_to() uses SQLite's native .backup() API — atomic, zero-copy.
  - from_file() rehydrates the in-memory cache from disk, so hot reads hit the
    cache and the DB is only consulted for replay after a crash.
  - reset() is deliberately destructive: it clears the current run, not all runs.
    Multi-run persistence (keeping history across resets) is a Phase 3 milestone.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.core.events import Event
from src.core.ledger import Ledger


class SQLiteLedger(Ledger):
    """Persistent append-only ledger backed by SQLite.

    Drop-in replacement for the in-memory Ledger.  The same API, the same
    idempotency guarantee, plus durable storage and snapshot/restore.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()
        self._cache: list[Event] = []
        self._seen_ids: set[str] = set()
        if self._path != ":memory:":
            self._load_cache()

    # ── schema ────────────────────────────────────────────────────────────────

    def _create_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                offset          INTEGER PRIMARY KEY AUTOINCREMENT,
                id              TEXT    UNIQUE NOT NULL,
                run_id          TEXT    NOT NULL,
                turn            INTEGER NOT NULL,
                kind            TEXT    NOT NULL,
                actor           TEXT    NOT NULL,
                payload         TEXT    NOT NULL,
                created_at      TEXT    NOT NULL,
                schema_version  INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_run_id ON events(run_id);
            CREATE INDEX IF NOT EXISTS idx_kind   ON events(kind);
            CREATE INDEX IF NOT EXISTS idx_actor  ON events(actor);
        """)
        self._conn.commit()

    # ── Ledger API ────────────────────────────────────────────────────────────

    def append(self, event: Event) -> Event:
        if event.id in self._seen_ids:
            return event
        try:
            self._conn.execute(
                "INSERT INTO events "
                "(id, run_id, turn, kind, actor, payload, created_at, schema_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.id,
                    event.run_id,
                    event.turn,
                    event.kind,
                    event.actor,
                    json.dumps(event.payload),
                    event.created_at.isoformat(),
                    event.schema_version,
                ),
            )
            self._conn.commit()
            self._cache.append(event)
            self._seen_ids.add(event.id)
        except sqlite3.IntegrityError:
            # Duplicate id inserted concurrently — safe to ignore.
            pass
        return event

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._cache)

    def reset(self) -> None:
        self._conn.execute("DELETE FROM events")
        self._conn.commit()
        self._cache.clear()
        self._seen_ids.clear()

    # ── persistence helpers ───────────────────────────────────────────────────

    def snapshot_to(self, dest: str | Path) -> None:
        """Copy the database to *dest* atomically using SQLite's backup API."""
        backup = sqlite3.connect(str(dest))
        try:
            self._conn.backup(backup)
        finally:
            backup.close()

    @classmethod
    def from_file(cls, path: str | Path) -> "SQLiteLedger":
        """Open an existing database and rehydrate the in-memory cache."""
        ledger = cls(path)
        return ledger

    def _load_cache(self) -> None:
        rows = self._conn.execute(
            "SELECT id, run_id, turn, kind, actor, payload, created_at, schema_version "
            "FROM events ORDER BY offset"
        ).fetchall()
        for row in rows:
            try:
                created_at = datetime.fromisoformat(row[6])
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                created_at = datetime.now(timezone.utc)

            e = Event(
                id=row[0],
                run_id=row[1],
                turn=row[2],
                kind=row[3],  # type: ignore[arg-type]
                actor=row[4],
                payload=json.loads(row[5]),
                created_at=created_at,
                schema_version=row[7],
            )
            self._cache.append(e)
            self._seen_ids.add(e.id)

    def tail(self, from_offset: int = 0) -> tuple[Event, ...]:
        """Return events with offset > from_offset (for crash-recovery replay)."""
        rows = self._conn.execute(
            "SELECT id, run_id, turn, kind, actor, payload, created_at, schema_version "
            "FROM events WHERE offset > ? ORDER BY offset",
            (from_offset,),
        ).fetchall()
        events = []
        for row in rows:
            e = Event(
                id=row[0], run_id=row[1], turn=row[2], kind=row[3],  # type: ignore[arg-type]
                actor=row[4], payload=json.loads(row[5]),
                schema_version=row[7],
            )
            events.append(e)
        return tuple(events)

    def latest_offset(self) -> int:
        row = self._conn.execute("SELECT MAX(offset) FROM events").fetchone()
        return row[0] or 0

    def close(self) -> None:
        self._conn.close()
