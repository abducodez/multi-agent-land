from __future__ import annotations

from src.core.conductor import Conductor
from src.core.registry import default_registry
from src.core.sqlite_ledger import SQLiteLedger


def _wood(ledger=None, **kw):
    reg = default_registry()
    return Conductor(reg.build_scenario("thousand-token-wood"), ledger=ledger, **kw)


class TestTokenMetering:
    def test_governor_accumulates_tokens(self):
        c = _wood()
        c.reset("a metered clearing")
        for _ in range(3):
            c.step()
        assert c.governor.stats["total_tokens"] > 0


class TestTwoClock:
    def test_step_n_ticks_advances_multiple_turns(self):
        c = _wood()
        c.reset("seed")
        start = c.turn
        c.step(n_ticks=5)
        assert c.turn == start + 5

    def test_default_step_is_one_tick(self):
        c = _wood()
        c.reset("seed")
        start = c.turn
        c.step()
        assert c.turn == start + 1


class TestRestore:
    def test_resume_from_persisted_ledger(self, tmp_path):
        db = tmp_path / "run.db"

        first = _wood(ledger=SQLiteLedger(str(db)))
        first.reset("a persistent wood")
        for _ in range(4):
            first.step()
        saved_run_id = first.run_id
        saved_turn = first.turn

        # New process: reopen the ledger and restore the conductor onto its tail.
        revived = _wood(ledger=SQLiteLedger.from_file(str(db)))
        assert revived.restore() is True
        assert revived.run_id == saved_run_id
        assert revived.turn == saved_turn

        # And it continues the same run rather than starting over.
        revived.step()
        assert revived.turn == saved_turn + 1

    def test_restore_empty_ledger_returns_false(self):
        c = _wood(ledger=SQLiteLedger(":memory:"))
        assert c.restore() is False


class TestSnapshots:
    def test_periodic_snapshot_written(self, tmp_path):
        db = tmp_path / "live.db"
        snap = tmp_path / "snap.db"
        c = _wood(ledger=SQLiteLedger(str(db)), snapshot_every=2, snapshot_path=str(snap))
        c.reset("snapshot me")
        for _ in range(4):
            c.step()
        assert snap.exists()
        assert len(SQLiteLedger.from_file(str(snap)).events) > 0
