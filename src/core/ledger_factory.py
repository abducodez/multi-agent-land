"""Backend selection for the event ledger — the durable store is required.

One decision lives here: which ``Ledger`` backend to construct.  The append-only
ledger is the single source of truth (ADR-0014); this only chooses *where* it is
durably stored.

A ``DATABASE_URL`` (Postgres/Neon, or any SQLAlchemy URL — e.g. ``sqlite://`` for
an in-memory store in tests) is **required**: the app persists to a real event
store and refuses to run without one.  Construction raises when no URL is
resolved rather than silently degrading to an ephemeral in-memory ledger.
"""

from __future__ import annotations

import os

from src.core.ledger import Ledger


def database_url() -> str | None:
    """Return a non-empty ``DATABASE_URL`` from the environment, else ``None``."""
    url = os.getenv("DATABASE_URL")
    return url or None


def _normalize_db_url(url: str) -> str:
    """Steer a bare Postgres URL to the installed psycopg3 driver.

    Neon (and most providers) hand out ``postgresql://`` / ``postgres://``, which
    SQLAlchemy maps to psycopg2 — but this project ships psycopg3, so a
    copy-pasted Neon URL would fail with a missing-driver error.
    Rewrite the bare scheme to ``postgresql+psycopg://``; URLs that already name a
    driver (``postgresql+...``) or use another backend (sqlite, …) pass through.
    """
    for scheme in ("postgresql://", "postgres://"):
        if url.startswith(scheme):
            return "postgresql+psycopg://" + url[len(scheme) :]
    return url


def make_ledger(url: str | None = None) -> Ledger:
    """Construct the durable ledger backend (required — never an in-memory fallback).

    *url* overrides ``DATABASE_URL`` (useful for tests/scripts — pass ``"sqlite://"``
    for an ephemeral in-memory store).  Raises :class:`RuntimeError` when neither is
    set: the app requires a real event store and must not silently run without one.
    """
    resolved = url or database_url()
    if not resolved:
        raise RuntimeError(
            "DATABASE_URL is required — the event store is not optional. "
            "Set DATABASE_URL (e.g. a Neon postgresql:// URL, or sqlite:///runs/events.db for "
            "a local file), or pass an explicit url to make_ledger()."
        )
    from src.core.sqlalchemy_ledger import SqlAlchemyLedger

    return SqlAlchemyLedger(_normalize_db_url(resolved))
