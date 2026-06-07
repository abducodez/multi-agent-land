from __future__ import annotations

import pytest

from src.core.events import Event, event_summary


def _event(**kwargs) -> Event:
    defaults = dict(run_id="r1", turn=1, kind="agent.spoke", actor="teller", payload={"text": "a line"})
    defaults.update(kwargs)
    return Event(**defaults)  # type: ignore[arg-type]


class TestEventSchema:
    def test_auto_id(self):
        e1 = _event()
        e2 = _event()
        assert e1.id != e2.id

    def test_explicit_id_preserved(self):
        e = _event(id="fixed-id")
        assert e.id == "fixed-id"

    def test_schema_version_default(self):
        assert _event().schema_version == 1

    def test_extra_fields_rejected(self):
        with pytest.raises(Exception):
            Event(run_id="r", turn=0, kind="agent.spoke", actor="x", payload={}, unknown="bad")  # type: ignore[call-arg]

    def test_invalid_kind_rejected(self):
        with pytest.raises(Exception):
            _event(kind="not.a.real.kind")


class TestEventSummary:
    def test_summary_includes_text(self):
        e = _event(payload={"text": "the moss glows"})
        summary = event_summary(e)
        assert "the moss glows" in summary

    def test_summary_includes_actor(self):
        e = _event(actor="seedkeeper")
        summary = event_summary(e)
        assert "seedkeeper" in summary

    def test_summary_falls_back_to_payload_when_no_text(self):
        e = _event(payload={"summary": "a brief"})
        summary = event_summary(e)
        assert "a brief" in summary
