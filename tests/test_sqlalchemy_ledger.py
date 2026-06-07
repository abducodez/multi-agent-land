from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.core.events import Event
from src.core.ledger import Ledger
from src.core.ledger_factory import make_ledger
from src.core.sqlalchemy_ledger import SqlAlchemyLedger


def _event(turn: int = 1, actor: str = "x") -> Event:
    return Event(run_id="r", turn=turn, kind="agent.spoke", actor=actor, payload={"text": "hi"})  # type: ignore[arg-type]


class TestSqlAlchemyLedgerBasic:
    def test_append_and_read(self):
        ledger = SqlAlchemyLedger()
        ledger.append(_event())
        assert len(ledger.events) == 1

    def test_idempotent(self):
        ledger = SqlAlchemyLedger()
        e = _event()
        ledger.append(e)
        ledger.append(e)
        assert len(ledger.events) == 1

    def test_ordered(self):
        ledger = SqlAlchemyLedger()
        for i in range(5):
            ledger.append(_event(turn=i))
        turns = [e.turn for e in ledger.events]
        assert turns == sorted(turns)

    def test_reset_clears(self):
        ledger = SqlAlchemyLedger()
        ledger.append(_event())
        ledger.reset()
        assert len(ledger.events) == 0

    def test_reset_allows_same_id(self):
        ledger = SqlAlchemyLedger()
        e = _event()
        ledger.append(e)
        ledger.reset()
        ledger.append(e)
        assert len(ledger.events) == 1

    def test_events_tuple(self):
        ledger = SqlAlchemyLedger()
        ledger.append(_event())
        assert isinstance(ledger.events, tuple)

    def test_payload_roundtrips(self):
        ledger = SqlAlchemyLedger()
        ledger.append(Event(run_id="r", turn=1, kind="clue.found", actor="a", payload={"n": 7, "tags": ["x"]}))  # type: ignore[arg-type]
        assert ledger.events[0].payload == {"n": 7, "tags": ["x"]}


class TestSqlAlchemyLedgerPersistence:
    def test_survives_reopen(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.db"
            ledger = SqlAlchemyLedger(path)
            ledger.append(_event(turn=42))
            ledger.close()

            ledger2 = SqlAlchemyLedger.from_file(path)
            assert len(ledger2.events) == 1
            assert ledger2.events[0].turn == 42

    def test_accepts_sqlite_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            url = f"sqlite:///{Path(tmpdir) / 'url.db'}"
            ledger = SqlAlchemyLedger(url)
            ledger.append(_event(turn=3))
            ledger.close()
            assert len(SqlAlchemyLedger.from_file(url).events) == 1

    def test_snapshot_and_restore(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "source.db"
            dst = Path(tmpdir) / "snap.db"

            ledger = SqlAlchemyLedger(src)
            for i in range(5):
                ledger.append(_event(turn=i))
            ledger.snapshot_to(dst)
            ledger.close()

            restored = SqlAlchemyLedger.from_file(dst)
            assert len(restored.events) == 5
            assert [e.turn for e in restored.events] == [0, 1, 2, 3, 4]

    def test_tail_returns_events_after_offset(self):
        ledger = SqlAlchemyLedger()
        for i in range(6):
            ledger.append(_event(turn=i))
        assert len(ledger.tail(from_offset=3)) == 3

    def test_latest_offset_matches_count(self):
        ledger = SqlAlchemyLedger()
        for i in range(4):
            ledger.append(_event(turn=i))
        assert ledger.latest_offset() == 4


class TestLedgerFactory:
    def test_offline_returns_in_memory_ledger(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        ledger = make_ledger()
        assert type(ledger) is Ledger

    def test_empty_database_url_is_offline(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "")
        assert type(make_ledger()) is Ledger

    def test_explicit_url_builds_sqlalchemy_backend(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = make_ledger(f"sqlite:///{Path(tmpdir) / 'factory.db'}")
            assert isinstance(ledger, SqlAlchemyLedger)
            ledger.close()

    def test_database_url_env_selects_backend(self, monkeypatch):
        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("DATABASE_URL", f"sqlite:///{Path(tmpdir) / 'env.db'}")
            ledger = make_ledger()
            assert isinstance(ledger, SqlAlchemyLedger)
            ledger.close()


# Postgres-only assertions run against a live Neon (or any Postgres) instance.
# Guarded so the suite stays green with no database connection (offline fallback).
@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="no DATABASE_URL (Postgres) configured")
class TestPostgresBackend:
    def test_append_and_idempotency_on_postgres(self):
        ledger = SqlAlchemyLedger.from_file(os.environ["DATABASE_URL"])
        ledger.reset()
        e = _event(turn=1)
        ledger.append(e)
        ledger.append(e)
        assert len(ledger.events) == 1
        assert ledger.latest_offset() >= 1
        ledger.reset()
        ledger.close()
