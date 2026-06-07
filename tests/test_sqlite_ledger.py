from __future__ import annotations

import tempfile
from pathlib import Path


from src.core.events import Event
from src.core.sqlite_ledger import SQLiteLedger


def _event(turn: int = 1, actor: str = "x") -> Event:
    return Event(run_id="r", turn=turn, kind="agent.spoke", actor=actor, payload={"text": "hi"})  # type: ignore[arg-type]


class TestSQLiteLedgerBasic:
    def test_append_and_read(self):
        ledger = SQLiteLedger()
        e = _event()
        ledger.append(e)
        assert len(ledger.events) == 1

    def test_idempotent(self):
        ledger = SQLiteLedger()
        e = _event()
        ledger.append(e)
        ledger.append(e)
        assert len(ledger.events) == 1

    def test_ordered(self):
        ledger = SQLiteLedger()
        for i in range(5):
            ledger.append(_event(turn=i))
        turns = [e.turn for e in ledger.events]
        assert turns == sorted(turns)

    def test_reset_clears(self):
        ledger = SQLiteLedger()
        ledger.append(_event())
        ledger.reset()
        assert len(ledger.events) == 0

    def test_reset_allows_same_id(self):
        ledger = SQLiteLedger()
        e = _event()
        ledger.append(e)
        ledger.reset()
        ledger.append(e)
        assert len(ledger.events) == 1

    def test_events_tuple(self):
        ledger = SQLiteLedger()
        ledger.append(_event())
        assert isinstance(ledger.events, tuple)


class TestSQLiteLedgerPersistence:
    def test_survives_reopen(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.db"
            ledger = SQLiteLedger(path)
            e = _event(turn=42)
            ledger.append(e)
            ledger.close()

            ledger2 = SQLiteLedger.from_file(path)
            assert len(ledger2.events) == 1
            assert ledger2.events[0].turn == 42

    def test_snapshot_and_restore(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "source.db"
            dst = Path(tmpdir) / "snap.db"

            ledger = SQLiteLedger(src)
            for i in range(5):
                ledger.append(_event(turn=i))
            ledger.snapshot_to(dst)
            ledger.close()

            restored = SQLiteLedger.from_file(dst)
            assert len(restored.events) == 5

    def test_tail_returns_events_after_offset(self):
        ledger = SQLiteLedger()
        for i in range(6):
            ledger.append(_event(turn=i))
        tail = ledger.tail(from_offset=3)
        assert len(tail) == 3

    def test_latest_offset_matches_count(self):
        ledger = SQLiteLedger()
        for i in range(4):
            ledger.append(_event(turn=i))
        assert ledger.latest_offset() == 4
