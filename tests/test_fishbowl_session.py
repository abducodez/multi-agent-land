"""End-to-end tests for ``FishbowlSession`` — live engine, offline stub, no mocks."""

from __future__ import annotations

from src.ui.fishbowl.session import FishbowlSession, scenario_titles


def test_scenario_titles_preferred_order() -> None:
    titles = scenario_titles()
    names = list(titles.values())
    # Preferred scenarios lead, in declared order.
    assert names[:3] == ["thousand-token-wood", "mystery-roots", "oracle-grove"]
    # Titles are non-empty display strings, all mapping to distinct internal names.
    assert all(title for title in titles)
    assert len(set(names)) == len(names)


def test_reset_writes_genesis() -> None:
    session = FishbowlSession("thousand-token-wood")
    session.reset()
    assert len(session.events) > 0
    assert session.events[0].kind == "run.started"


def test_step_grows_the_ledger() -> None:
    session = FishbowlSession("thousand-token-wood")
    session.reset()
    before = len(session.events)
    session.step()
    assert len(session.events) > before


def test_snapshot_contract_keys() -> None:
    session = FishbowlSession("thousand-token-wood")
    session.reset()
    session.step()
    snap = session.snapshot()
    for key in ("cast", "feed", "tokens", "step", "total"):
        assert key in snap
    assert snap["total"] == len(session.events)
    assert snap["step"] == len(session.events)
    assert isinstance(snap["cast"], list) and snap["cast"]


def test_snapshot_scrubs_to_prefix() -> None:
    session = FishbowlSession("thousand-token-wood")
    session.reset()
    session.step()
    session.step()
    head = session.snapshot()
    scrubbed = session.snapshot(1)
    assert scrubbed["step"] == 1
    assert scrubbed["total"] == head["total"]
    # A prefix view never has more feed items than the head.
    assert len(scrubbed["feed"]) <= len(head["feed"])


def test_inject_records_labelled_user_event_and_advances() -> None:
    session = FishbowlSession("thousand-token-wood")
    session.reset()
    step_before = session.snapshot()["step"]
    session.inject("a lantern hums", label="GUST")

    injected = [e for e in session.events if e.kind == "user.injected"]
    assert injected, "expected a user.injected event"
    assert injected[-1].payload.get("text") == "a lantern hums"
    assert injected[-1].payload.get("label") == "GUST"

    assert session.snapshot()["step"] > step_before
