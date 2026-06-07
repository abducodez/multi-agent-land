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

    def test_malformed_kind_rejected(self):
        # The schema validates SHAPE, not membership: a kind must be a
        # lowercase, dot-namespaced identifier.  These are malformed.
        for bad in ("nodot", "Bad.Kind", "bad kind", "trailing.", ".leading", "two..dots"):
            with pytest.raises(Exception):
                _event(kind=bad)

    def test_custom_namespaced_kind_allowed(self):
        # Modularity contract (ADR-0009): a scenario may mint new kinds without
        # editing core.  Well-formed custom kinds validate cleanly.
        for ok in ("clue.found", "hypothesis.proposed", "episode.published", "image.generated"):
            e = _event(kind=ok)
            assert e.kind == ok

    def test_core_kinds_are_valid(self):
        from src.core.events import CORE_EVENT_KINDS, is_valid_kind

        assert all(is_valid_kind(k) for k in CORE_EVENT_KINDS)
        assert "agent.reflected" in CORE_EVENT_KINDS


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
