from __future__ import annotations

import pytest

from src.core.events import Event
from src.core.ledger import Ledger


def _make_event(turn: int = 0, kind: str = "agent.spoke", actor: str = "x") -> Event:
    return Event(run_id="run-test", turn=turn, kind=kind, actor=actor, payload={"text": "hello"})  # type: ignore[arg-type]


class TestLedgerAppend:
    def test_append_single(self):
        ledger = Ledger()
        e = _make_event()
        ledger.append(e)
        assert len(ledger.events) == 1

    def test_append_returns_event(self):
        ledger = Ledger()
        e = _make_event()
        returned = ledger.append(e)
        assert returned is e

    def test_idempotent_on_same_id(self):
        ledger = Ledger()
        e = _make_event()
        ledger.append(e)
        ledger.append(e)
        assert len(ledger.events) == 1

    def test_events_are_ordered(self):
        ledger = Ledger()
        e1 = _make_event(turn=1)
        e2 = _make_event(turn=2)
        ledger.append(e1)
        ledger.append(e2)
        assert ledger.events[0].turn == 1
        assert ledger.events[1].turn == 2

    def test_events_returns_immutable_tuple(self):
        ledger = Ledger()
        ledger.append(_make_event())
        result = ledger.events
        assert isinstance(result, tuple)

    def test_extend(self):
        ledger = Ledger()
        events = [_make_event(turn=i) for i in range(3)]
        ledger.extend(events)
        assert len(ledger.events) == 3

    def test_extend_deduplicates(self):
        ledger = Ledger()
        e = _make_event()
        ledger.extend([e, e])
        assert len(ledger.events) == 1

    def test_reset_clears(self):
        ledger = Ledger()
        ledger.append(_make_event())
        ledger.reset()
        assert len(ledger.events) == 0

    def test_reset_allows_same_id_again(self):
        ledger = Ledger()
        e = _make_event()
        ledger.append(e)
        ledger.reset()
        ledger.append(e)
        assert len(ledger.events) == 1
