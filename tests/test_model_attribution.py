"""Per-event model attribution (ADR-0028): each agent line records the model that
actually produced it — the route key it asked for (``model_profile``) and the
concrete model that ran (``model_id``) — and that survives the SQL round-trip and
surfaces on the Show's cast cards.

No mocks: the deterministic stub drives the cast, so ``model_id`` reads ``stub:<tier>``
offline — the same envelope a live Modal/HF run fills with the served model id.
"""

from __future__ import annotations

import pytest

from src.core.ledger_factory import make_ledger
from src.core.registry import default_registry
from src.ui.fishbowl.adapter import short_model_name
from src.ui.fishbowl.session import FishbowlSession


@pytest.fixture
def shared_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'events.db'}")


def _first_scenario() -> str:
    return next(iter(default_registry().scenarios))


def _run_with_lines(session_id: str = "u1") -> FishbowlSession:
    session = FishbowlSession(_first_scenario())
    session.reset("seed", session_id=session_id)
    for _ in range(session.autoplay_tick_cap):
        events = session.events
        if sum(1 for e in events if e.model_id) >= 2:
            break
        try:
            if not session.step_one():
                break
        except Exception:
            break
    return session


class TestShortModelName:
    def test_strips_org_prefix(self):
        assert short_model_name("openai/openbmb/MiniCPM4.1-8B") == "MiniCPM4.1-8B"
        assert short_model_name("google/gemma-4-12B") == "gemma-4-12B"

    def test_leaves_stub_and_empty_alone(self):
        assert short_model_name("stub:fast") == "stub:fast"
        assert short_model_name("") == ""
        assert short_model_name(None) == ""  # type: ignore[arg-type]


class TestEventModelAttribution:
    def test_agent_events_record_profile_and_model(self, shared_db):
        session = _run_with_lines()
        produced = [e for e in session.events if e.model_id]
        assert produced, "stub cast should have produced at least one model-backed line"
        for e in produced:
            # Offline, the route key is a tier and the model is its stub.
            assert e.model_profile  # the route key the agent asked for
            assert e.model_id == f"stub:{e.model_profile}" or e.model_id.startswith("stub:")

    def test_scenario_and_genesis_events_have_no_model(self, shared_db):
        session = _run_with_lines()
        for e in session.events:
            if e.kind in ("run.started", "run.finished") or e.actor == "conductor":
                assert e.model_id is None and e.model_profile is None

    def test_model_attribution_survives_sql_round_trip(self, shared_db):
        session = _run_with_lines()
        run_id = session.conductor.run_id
        # A fresh ledger connection re-reads rows from disk — envelope must persist.
        reread = make_ledger().events_for_run(run_id)
        produced = [e for e in reread if e.model_id]
        assert produced
        assert all(e.model_profile for e in produced)


class TestCardSurfacesActualModel:
    def test_card_model_reflects_the_model_that_ran(self, shared_db):
        session = _run_with_lines()
        vm = session.snapshot()
        spoken_actors = {e.actor for e in session.events if e.model_id}
        cards = {c["id"]: c for c in vm["cast"]}
        # Every actor that produced a line shows its actual (stub) model on the card.
        for actor in spoken_actors & cards.keys():
            assert cards[actor]["model_id"] is not None
            assert cards[actor]["model"] == short_model_name(cards[actor]["model_id"])
