"""Backend selection for the event ledger — env-gated, offline by default.

One decision lives here: which ``Ledger`` backend to construct.  The append-only
ledger is the single source of truth (ADR-0014); this only chooses *where* it is
durably stored.

  - ``DATABASE_URL`` set  → :class:`SqlAlchemyLedger` (Postgres/Neon or any
    SQLAlchemy URL), the durable event store.
  - ``DATABASE_URL`` unset → the in-memory :class:`Ledger`.

With no ``DATABASE_URL`` the system never imports SQLAlchemy or a database driver,
so the offline path stays import-clean and fully testable without a server.
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
    SQLAlchemy maps to psycopg2 — but this project ships psycopg3 (the ``store``
    extra), so a copy-pasted Neon URL would fail with a missing-driver error.
    Rewrite the bare scheme to ``postgresql+psycopg://``; URLs that already name a
    driver (``postgresql+...``) or use another backend (sqlite, …) pass through.
    """
    for scheme in ("postgresql://", "postgres://"):
        if url.startswith(scheme):
            return "postgresql+psycopg://" + url[len(scheme) :]
    return url


def make_ledger(url: str | None = None) -> Ledger:
    """Construct the configured ledger backend.

    *url* overrides ``DATABASE_URL`` (useful for tests/scripts).  When neither is
    set, returns the in-memory ``Ledger``.  ``SqlAlchemyLedger`` is imported lazily
    so the offline path does not require SQLAlchemy to be installed.
    """
    resolved = url or database_url()
    if not resolved:
        return Ledger()
    from src.core.sqlalchemy_ledger import SqlAlchemyLedger

    return SqlAlchemyLedger(_normalize_db_url(resolved))
