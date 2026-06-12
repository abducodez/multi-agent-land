"""Sessions & Archive: per-user run attribution, run-scoped live transcript, and the
read-only replay the Lab's "Load" drawer hands to the Show.

No mocks — the deterministic stub (conftest) drives the cast.  These tests use a
*file-backed* SQLite DB (not the suite's default in-memory ``sqlite://``) because the
Archive reads through a fresh ``make_ledger()`` each call, and only a file is shared
across those connections — exactly how the real app's many sessions share one store.
"""

from __future__ import annotations

import pytest

from src.core.conductor import Conductor
from src.core.events import Event, normalize_session_id
from src.core.ledger_factory import make_ledger
from src.core.manifest import AgentManifest, ScheduleConfig
from src.core.registry import default_registry
from src.scenarios.base import Scenario
from src.ui.fishbowl import app as fb_app
from src.ui.fishbowl.archive import list_runs, load_replay, run_card_label
from src.ui.fishbowl.session import FishbowlSession, ReplaySession


@pytest.fixture
def shared_db(monkeypatch, tmp_path):
    """Point every ``make_ledger()`` at one on-disk SQLite file (shared across sessions)."""
    url = f"sqlite:///{tmp_path / 'events.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    return url


def _two_scenarios() -> tuple[str, str]:
    names = list(default_registry().scenarios)
    assert len(names) >= 2, "need at least two scenarios to test cross-scenario scoping"
    return names[0], names[1]


def _run(scenario_name: str, session_id: str, *, seed: str = "seed") -> str:
    """Start a run for *session_id* in *scenario_name*; return its run_id."""
    session = FishbowlSession(scenario_name)
    session.reset(seed, session_id=session_id)
    return session.conductor.run_id


# ── per-user attribution + scoping ────────────────────────────────────────────────


class TestRunAttribution:
    def test_run_started_carries_session_id(self, shared_db):
        scenario, _ = _two_scenarios()
        run_id = _run(scenario, "user-A")
        started = next(e for e in make_ledger().events_for_run(run_id) if e.kind == "run.started")
        assert started.payload.get("session_id") == "user-A"

    def test_no_session_id_omits_the_key(self, shared_db):
        scenario, _ = _two_scenarios()
        session = FishbowlSession(scenario)
        session.reset("seed")  # no session_id
        started = next(e for e in make_ledger().events_for_run(session.conductor.run_id) if e.kind == "run.started")
        assert "session_id" not in started.payload


class TestLiveTranscriptIsRunScoped:
    def test_session_events_never_bleed_other_runs(self, shared_db):
        # The core fix: the ledger is one shared store of every run, but a live
        # session must only ever see its own run — no cross-run/scenario bleed.
        sx, sy = _two_scenarios()
        a = FishbowlSession(sx)
        a.reset("seed", session_id="user-A")
        a_run = a.conductor.run_id

        b = FishbowlSession(sy)  # a different world, same shared DB
        b.reset("seed", session_id="user-A")

        # b's ledger physically contains a's events (shared file)...
        assert any(e.run_id == a_run for e in b.conductor.ledger.events)
        # ...yet b's *view* shows only b's run, and a's only a's.
        assert {e.run_id for e in b.events} == {b.conductor.run_id}
        assert {e.run_id for e in a.events} == {a_run}


# ── the Archive list ──────────────────────────────────────────────────────────────


class TestListRuns:
    def test_filters_to_my_runs_in_this_world(self, shared_db):
        sx, sy = _two_scenarios()
        mine_x = _run(sx, "me")
        _run(sx, "someone-else")  # same world, different user
        _run(sy, "me")  # my run, different world

        rows = list_runs(sx, "me")
        assert [r.run_id for r in rows] == [mine_x]
        assert rows[0].session_id == "me"
        assert rows[0].scenario == sx

    def test_newest_first(self, shared_db):
        sx, _ = _two_scenarios()
        first = _run(sx, "me", seed="one")
        second = _run(sx, "me", seed="two")
        rows = list_runs(sx, "me")
        assert [r.run_id for r in rows] == [second, first]

    def test_empty_without_session_id(self, shared_db):
        sx, _ = _two_scenarios()
        _run(sx, "me")
        assert list_runs(sx, None) == []
        assert list_runs(sx, "") == []

    def test_run_card_label_is_a_single_line(self, shared_db):
        sx, _ = _two_scenarios()
        _run(sx, "me")
        label = run_card_label(list_runs(sx, "me")[0])
        assert "\n" not in label and label.startswith("▶")


# ── read-only replay ───────────────────────────────────────────────────────────────


class TestReplay:
    def test_load_replay_reconstructs_the_run(self, shared_db):
        sx, _ = _two_scenarios()
        run_id = _run(sx, "me")
        expected = make_ledger().events_for_run(run_id)

        replay = load_replay(run_id)
        assert isinstance(replay, ReplaySession)
        assert replay.replay is True
        assert [e.id for e in replay.events] == [e.id for e in expected]
        assert replay.scenario_name == sx
        # The snapshot renders without a live conductor.
        vm = replay.snapshot()
        assert vm["total"] == len(expected)

    def test_load_replay_unknown_run_is_none(self, shared_db):
        assert load_replay("no-such-run") is None
        assert load_replay("") is None

    def test_replay_session_is_inert(self, shared_db):
        sx, _ = _two_scenarios()
        replay = load_replay(_run(sx, "me"))
        head_before = replay.head
        assert replay.step_one() is False
        replay.step()
        replay.inject("nudge")
        assert replay.head == head_before  # nothing generated

    def test_advance_replays_prefix_then_stops(self, shared_db):
        sx, _ = _two_scenarios()
        replay = load_replay(_run(sx, "me"))
        assert replay.head >= 1
        # From 0 it walks forward through the recorded prefix without generating.
        k, ticks, reason = fb_app.advance_one_tick(replay, 0, 0)
        assert (k, reason) == (1, None)
        # At the head it stops cleanly (no token spend, no backstop trip).
        k, ticks, reason = fb_app.advance_one_tick(replay, replay.head, 0)
        assert k == replay.head and reason is not None


# ── session hardening: envelope stamping, normalization, context scoping ──────────


class _ProbeAgent:
    """Records the run_ids of every event the conductor hands it as context."""

    name = "probe"
    manifest = AgentManifest(name="probe", persona="p", may_emit=["agent.spoke"], schedule=ScheduleConfig(tick_every=1))

    def __init__(self) -> None:
        self.last_usage: dict = {}
        self.seen_run_ids: set[str] = set()

    def act(self, run_id, turn, projection, recent_events) -> Event:
        self.seen_run_ids.update(e.run_id for e in recent_events)
        return Event(run_id=run_id, turn=turn, kind="agent.spoke", actor="probe", payload={"text": "t"})


def _probe_conductor() -> tuple[Conductor, _ProbeAgent]:
    probe = _ProbeAgent()
    scenario = Scenario(name="probe-world", default_seed="seed", agents=(probe,))
    return Conductor(scenario, ledger=make_ledger()), probe


class TestNormalizeSessionId:
    def test_accepts_minted_shapes(self):
        assert normalize_session_id("3b456aab-4ac6-46d6-981a-ff10a8f25fdb") is not None
        assert normalize_session_id("sess-abc123xyz") == "sess-abc123xyz"
        assert normalize_session_id("  padded-id  ") == "padded-id"

    def test_rejects_untrusted_garbage(self):
        assert normalize_session_id(None) is None
        assert normalize_session_id("") is None
        assert normalize_session_id("a" * 65) is None  # over-long
        assert normalize_session_id("has spaces") is None
        assert normalize_session_id("<script>alert(1)</script>") is None

    def test_conductor_normalizes_at_the_boundary(self, shared_db):
        c, _probe = _probe_conductor()
        c.reset("seed", session_id="<not a valid id>")
        assert c.session_id is None
        started = next(e for e in c.ledger.events_for_run(c.run_id) if e.kind == "run.started")
        assert "session_id" not in started.payload and started.session_id is None


class TestEnvelopeStamping:
    def test_every_event_in_a_run_carries_the_session_id(self, shared_db):
        c, _probe = _probe_conductor()
        c.reset("seed", session_id="user-A")
        c.step(2)  # genesis + agent turns → several event kinds
        events = c.ledger.events_for_run(c.run_id)
        assert len(events) >= 3
        assert all(e.session_id == "user-A" for e in events)

    def test_session_id_round_trips_through_sql(self, shared_db):
        c, _probe = _probe_conductor()
        c.reset("seed", session_id="user-A")
        c.step(1)
        # A fresh connection re-reads rows from disk — the envelope must survive.
        reread = make_ledger().events_for_run(c.run_id)
        assert reread and all(e.session_id == "user-A" for e in reread)

    def test_unattributed_run_stays_null(self, shared_db):
        c, _probe = _probe_conductor()
        c.reset("seed")  # headless run, no session
        c.step(1)
        assert all(e.session_id is None for e in c.ledger.events_for_run(c.run_id))


class TestAgentContextIsRunScoped:
    def test_agents_never_see_other_runs_in_recent_events(self, shared_db):
        # Run A fills the shared store with another user's discussion.
        a, _ = _probe_conductor()
        a.reset("seed", session_id="user-A")
        a.step(2)
        a_run = a.run_id

        # Run B (fresh conductor, same DB) — its agent's context must be B-only.
        b, probe_b = _probe_conductor()
        b.reset("seed", session_id="user-B")
        b.step(2)

        assert any(e.run_id == a_run for e in b.ledger.events)  # store IS shared...
        assert probe_b.seen_run_ids == {b.run_id}  # ...but the agent's context is not
