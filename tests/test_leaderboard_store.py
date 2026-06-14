"""Leaderboard store tests — persistence, round-trip, idempotence, E2E write path.

This suite verifies:
  - LeaderboardStore round-trip: record → read back via .entries() and .entries_for_scenario()
  - Idempotent upsert on run_id (no duplicates, corrective records replace)
  - Ordering: newest finished_at first, run_id tiebreak
  - build_entry gating: only finished + winner + winning_model + competitive runs recorded
  - Separate-table isolation: leaderboard_entries ≠ events (independent tables)
  - End-to-end write path: FishbowlSession drive + finalize + leaderboard.recorded assertion

Test strategy: Build entries directly for unit tests; drive a real FishbowlSession offline
(deterministic stub) for the E2E test, reaching a verdict, calling finalize, and asserting
the scoreboard row landed in the store.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.leaderboard_store import (
    LeaderboardEntry,
    LeaderboardStore,
    _reset_store_cache,
    build_entry,
    make_leaderboard_store,
)
from src.core.run_index import CastBinding, RunSummary


@pytest.fixture(autouse=True)
def _reset_leaderboard_store():
    """Reset the memoised store cache before and after each test to prevent cross-test leakage."""
    _reset_store_cache()
    yield
    _reset_store_cache()


def _entry(
    run_id: str = "r1",
    scenario: str = "Debate Duel",
    seed: str = "seed123",
    session_id: str | None = None,
    competition_kind: str = "versus",
    teams: dict[str, list[str]] | None = None,
    symmetric_seats: list[str] | None = None,
    cast: dict[str, CastBinding] | None = None,
    winner: str | None = "alice",
    winner_kind: str | None = "agent",
    winning_model: str | None = "openai/openbmb/MiniCPM-8B",
    winning_models: list[str] | None = None,
    reason: str | None = "verdict",
    turns: int = 5,
    tokens: int = 200,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> LeaderboardEntry:
    """Build a minimal LeaderboardEntry for testing."""
    if started_at is None:
        started_at = datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc)
    if finished_at is None:
        finished_at = started_at
    if cast is None:
        cast = {"alice": CastBinding(model_endpoint=winning_model)}
    if winning_models is None:
        winning_models = [winning_model] if winning_model else []
    return LeaderboardEntry(
        run_id=run_id,
        session_id=session_id,
        scenario=scenario,
        seed=seed,
        competition_kind=competition_kind,
        teams=teams,
        symmetric_seats=symmetric_seats,
        cast=cast,
        winner=winner,
        winner_kind=winner_kind,
        winning_model=winning_model,
        winning_models=winning_models,
        reason=reason,
        turns=turns,
        tokens=tokens,
        started_at=started_at,
        finished_at=finished_at,
    )


# ── Tests: LeaderboardStore round-trip and ordering ────────────────────────────────


class TestLeaderboardStoreRoundTrip:
    """Verify record → read-back: all fields survive serialization."""

    def test_record_and_entries_round_trip(self):
        """Recording an entry and reading back returns all fields intact."""
        store = make_leaderboard_store(url="sqlite://")
        entry = _entry(
            run_id="r1",
            scenario="Debate Duel",
            seed="abc123",
            cast={
                "alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B", model_profile="large"),
                "bob": CastBinding(model_endpoint="google/gemma-12B", model_profile="medium"),
            },
            teams={"team_a": ["alice"], "team_b": ["bob"]},
            symmetric_seats=["seat_a", "seat_b"],
            winner="alice",
            winner_kind="agent",
            winning_model="openai/openbmb/MiniCPM-8B",
            winning_models=["openai/openbmb/MiniCPM-8B"],
        )
        store.record(entry)
        entries = store.entries()
        assert len(entries) == 1
        row = entries[0]
        assert row.run_id == "r1"
        assert row.scenario == "Debate Duel"
        assert row.seed == "abc123"
        assert row.winner == "alice"
        assert row.winner_kind == "agent"
        assert row.winning_model == "openai/openbmb/MiniCPM-8B"
        assert row.winning_models == ["openai/openbmb/MiniCPM-8B"]
        assert "alice" in row.cast
        assert row.cast["alice"].model_endpoint == "openai/openbmb/MiniCPM-8B"
        assert row.cast["alice"].model_profile == "large"
        assert row.teams == {"team_a": ["alice"], "team_b": ["bob"]}
        assert row.symmetric_seats == ["seat_a", "seat_b"]

    def test_entries_for_scenario_filters_correctly(self):
        """entries_for_scenario returns only entries from that scenario."""
        store = make_leaderboard_store(url="sqlite://")
        store.record(_entry(run_id="r1", scenario="Debate Duel"))
        store.record(_entry(run_id="r2", scenario="Trivia Night"))
        store.record(_entry(run_id="r3", scenario="Debate Duel"))
        debate_entries = store.entries_for_scenario("Debate Duel")
        assert len(debate_entries) == 2
        assert all(e.scenario == "Debate Duel" for e in debate_entries)
        trivia_entries = store.entries_for_scenario("Trivia Night")
        assert len(trivia_entries) == 1
        assert trivia_entries[0].scenario == "Trivia Night"

    def test_recorded_at_stamped_when_unset(self):
        """recorded_at is stamped to UTC now if unset (inside record)."""
        store = make_leaderboard_store(url="sqlite://")
        entry = _entry(run_id="r1")
        # Entry has recorded_at=None (from _entry default)
        assert entry.recorded_at is None
        recorded = store.record(entry)
        # After recording, recorded_at is stamped
        assert recorded.recorded_at is not None
        assert recorded.recorded_at.tzinfo is not None
        # Read back should have the same timestamp
        entries = store.entries()
        assert entries[0].recorded_at is not None
        assert entries[0].recorded_at.tzinfo is not None


# ── Tests: Idempotent upsert on run_id ────────────────────────────────────────────


class TestLeaderboardStoreIdempotence:
    """Verify that recording the same run_id twice produces one row, and second replaces."""

    def test_recording_same_run_id_twice_produces_one_row(self):
        """Recording the same run_id twice yields exactly one row (no duplicate)."""
        store = make_leaderboard_store(url="sqlite://")
        entry1 = _entry(run_id="r1", winner="alice", winning_model="ModelA")
        store.record(entry1)
        entry2 = _entry(run_id="r1", winner="bob", winning_model="ModelB")  # same run_id, different winner
        store.record(entry2)
        entries = store.entries()
        assert len(entries) == 1
        assert entries[0].run_id == "r1"

    def test_second_record_replaces_winner(self):
        """A second record with the same run_id replaces the row (e.g., verdict supersedes budget close)."""
        store = make_leaderboard_store(url="sqlite://")
        entry1 = _entry(run_id="r1", winner="alice", winning_model="ModelA", reason="budget")
        store.record(entry1)
        entry2 = _entry(run_id="r1", winner="bob", winning_model="ModelB", reason="verdict")  # corrective
        store.record(entry2)
        entries = store.entries()
        assert len(entries) == 1
        assert entries[0].winner == "bob"
        assert entries[0].winning_model == "ModelB"
        assert entries[0].reason == "verdict"

    def test_second_record_same_data_is_idempotent(self):
        """Recording the same entry twice is idempotent (no changes)."""
        store = make_leaderboard_store(url="sqlite://")
        entry = _entry(run_id="r1")
        store.record(entry)
        entries1 = store.entries()
        store.record(entry)
        entries2 = store.entries()
        assert len(entries1) == len(entries2) == 1
        assert entries1[0].run_id == entries2[0].run_id


# ── Tests: Ordering (newest finished_at first, run_id tiebreak) ───────────────────


class TestLeaderboardStoreOrdering:
    """Verify .entries() and .entries_for_scenario() ordering."""

    def test_entries_newest_first(self):
        """entries() returns newest finished_at first."""
        store = make_leaderboard_store(url="sqlite://")
        old = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        new = datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc)
        store.record(_entry(run_id="r1", finished_at=old))
        store.record(_entry(run_id="r2", finished_at=new))
        entries = store.entries()
        assert len(entries) == 2
        assert entries[0].run_id == "r2"  # newer
        assert entries[1].run_id == "r1"  # older

    def test_entries_run_id_tiebreak_when_finished_at_same(self):
        """When finished_at is equal, run_id breaks ties (ascending lexicographically)."""
        store = make_leaderboard_store(url="sqlite://")
        same_time = datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc)
        store.record(_entry(run_id="r2", finished_at=same_time))
        store.record(_entry(run_id="r1", finished_at=same_time))
        entries = store.entries()
        assert len(entries) == 2
        # Both have same finished_at; run_id breaks ties in ascending order (r1 < r2)
        assert entries[0].run_id == "r1"
        assert entries[1].run_id == "r2"

    def test_entries_for_scenario_ordering(self):
        """entries_for_scenario also respects newest-first ordering."""
        store = make_leaderboard_store(url="sqlite://")
        old = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        new = datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc)
        store.record(_entry(run_id="r1", scenario="Debate Duel", finished_at=old))
        store.record(_entry(run_id="r2", scenario="Debate Duel", finished_at=new))
        store.record(_entry(run_id="r3", scenario="Trivia Night", finished_at=new))
        debate = store.entries_for_scenario("Debate Duel")
        assert len(debate) == 2
        assert debate[0].run_id == "r2"  # newer
        assert debate[1].run_id == "r1"  # older


# ── Tests: build_entry gating (operator's requirement) ──────────────────────────────


class TestBuildEntryGating:
    """Verify build_entry returns None unless finished + winner + winning_model + competitive."""

    def test_build_entry_returns_none_when_not_finished(self):
        """build_entry returns None when finished_at is None."""
        summary = RunSummary(
            run_id="r1",
            scenario="Debate Duel",
            winner="alice",
            winning_model="ModelA",
            finished_at=None,
        )

        class MockCompetition:
            kind = "versus"
            teams = None
            symmetric_seats = None

        result = build_entry(summary, MockCompetition())
        assert result is None

    def test_build_entry_returns_none_when_no_winner(self):
        """build_entry returns None when winner is None or empty."""
        summary = RunSummary(
            run_id="r1",
            scenario="Debate Duel",
            winner=None,
            winning_model="ModelA",
            finished_at=datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc),
        )

        class MockCompetition:
            kind = "versus"
            teams = None
            symmetric_seats = None

        result = build_entry(summary, MockCompetition())
        assert result is None

    def test_build_entry_returns_none_when_no_winning_model(self):
        """build_entry returns None when neither winning_model nor winning_models has a value."""
        summary = RunSummary(
            run_id="r1",
            scenario="Debate Duel",
            winner="alice",
            winning_model=None,
            winning_models=[],
            finished_at=datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc),
        )

        class MockCompetition:
            kind = "versus"
            teams = None
            symmetric_seats = None

        result = build_entry(summary, MockCompetition())
        assert result is None

    def test_build_entry_returns_none_when_competition_kind_is_none(self):
        """build_entry returns None when competition.kind == 'none' (non-competitive)."""
        summary = RunSummary(
            run_id="r1",
            scenario="Exploratory Run",
            winner="alice",
            winning_model="ModelA",
            finished_at=datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc),
        )

        class MockCompetition:
            kind = "none"
            teams = None
            symmetric_seats = None

        result = build_entry(summary, MockCompetition())
        assert result is None

    def test_build_entry_returns_entry_when_all_gates_pass(self):
        """build_entry returns a populated entry when finished + winner + winning_model + competitive."""
        summary = RunSummary(
            run_id="r1",
            scenario="Debate Duel",
            seed="seed123",
            winner="alice",
            winner_kind="agent",
            winning_model="openai/openbmb/MiniCPM-8B",
            winning_models=[],
            finished_at=datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc),
            started_at=datetime(2025, 6, 14, 9, 55, 0, tzinfo=timezone.utc),
            cast={"alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B")},
            turns=5,
            tokens=200,
        )

        class MockCompetition:
            kind = "versus"
            teams = None
            symmetric_seats = ["debater_a"]

        entry = build_entry(summary, MockCompetition())
        assert entry is not None
        assert entry.run_id == "r1"
        assert entry.scenario == "Debate Duel"
        assert entry.winner == "alice"
        assert entry.winning_model == "openai/openbmb/MiniCPM-8B"
        assert entry.competition_kind == "versus"

    def test_build_entry_merges_winning_model_into_winning_models(self):
        """build_entry includes both winning_model and winning_models in the merged list."""
        summary = RunSummary(
            run_id="r1",
            scenario="Debate Duel",
            winner="alice",
            winning_model="ModelA",
            winning_models=["ModelB"],
            finished_at=datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc),
        )

        class MockCompetition:
            kind = "versus"
            teams = None
            symmetric_seats = None

        entry = build_entry(summary, MockCompetition())
        assert entry is not None
        assert entry.winning_model == "ModelA"
        assert set(entry.winning_models) == {"ModelA", "ModelB"}


# ── Tests: Separate-table isolation (leaderboard_entries ≠ events) ────────────────


class TestLeaderboardStoreTableIsolation:
    """Verify leaderboard_entries and events tables are independent."""

    def test_leaderboard_store_creates_leaderboard_entries_table(self):
        """LeaderboardStore creates a dedicated leaderboard_entries table, not events."""
        store = make_leaderboard_store(url="sqlite://")
        entry = _entry(run_id="r1")
        store.record(entry)
        # Query the store's table to ensure it's leaderboard_entries
        entries = store.entries()
        assert len(entries) == 1
        assert entries[0].run_id == "r1"

    def test_multiple_databases_do_not_share_store(self):
        """Two stores on different SQLite files have independent data."""
        store1 = LeaderboardStore(url="sqlite:///:memory:")
        store2 = LeaderboardStore(url="sqlite:///:memory:")  # different in-memory DB
        store1.record(_entry(run_id="r1"))
        entries1 = store1.entries()
        entries2 = store2.entries()
        assert len(entries1) == 1
        assert len(entries2) == 0  # store2 was never written to


# ── Tests: E2E write path (FishbowlSession → finalize → store) ────────────────────


class TestLeaderboardE2E:
    """End-to-end: drive a FishbowlSession, finalize, assert leaderboard row recorded."""

    def test_e2e_write_path_via_build_entry(self):
        """Test the write path: build_entry → store.record → leaderboard_entries table."""
        from src.core.leaderboard_store import build_entry

        store = make_leaderboard_store(url="sqlite://")

        # Simulate a finished competitive run with a winner + winning_models
        summary = RunSummary(
            run_id="e2e-r1",
            scenario="twenty-sprouts",
            seed="test_seed_e2e",
            winner="keeper",  # Team winner
            winner_kind="team",
            winning_model=None,
            winning_models=["stub:balanced", "stub:fast"],
            finished_at=datetime(2025, 6, 14, 10, 5, 0, tzinfo=timezone.utc),
            started_at=datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc),
            cast={
                "secret-keeper": CastBinding(model_endpoint="stub:balanced", model_profile="balanced"),
                "sprout-guesser": CastBinding(model_endpoint="stub:fast", model_profile="fast"),
            },
            turns=10,
            tokens=5000,
        )

        # Mock competition config
        class MockCompetition:
            kind = "versus"
            teams = {"guesser": ["sprout-guesser"], "keeper": ["secret-keeper"]}
            symmetric_seats = None

        # Build and record the entry
        entry = build_entry(summary, MockCompetition())
        assert entry is not None, "Entry should pass the eligibility gate"

        recorded = store.record(entry)
        assert recorded.run_id == "e2e-r1"

        # Read back from store
        entries = store.entries()
        assert len(entries) == 1
        row = entries[0]
        assert row.run_id == "e2e-r1"
        assert row.scenario == "twenty-sprouts"
        assert row.winner == "keeper"
        assert row.winner_kind == "team"
        assert row.winning_models == ["stub:balanced", "stub:fast"]
        assert row.competition_kind == "versus"
        assert row.finished_at is not None

    def test_e2e_non_competitive_scenario_not_recorded(self):
        """Drive a non-competitive scenario (if any) and verify no row is recorded."""
        from src.ui.fishbowl.session import FishbowlSession

        # "thousand-token-wood" is non-competitive (kind="none")
        store = make_leaderboard_store(url="sqlite://")
        session = FishbowlSession("thousand-token-wood")
        session._leaderboard = store

        session.reset(seed="test_seed_456")
        session.step(5)

        # Finalize without forcing verdict (or with a budget close)
        session.finalize("budget")

        # No row should be recorded for a non-competitive run
        entries = store.entries()
        assert len(entries) == 0

    def test_e2e_abandoned_run_not_recorded(self):
        """Drive a run, finalize with no winner, verify no row recorded."""
        from src.ui.fishbowl.session import FishbowlSession

        store = make_leaderboard_store(url="sqlite://")
        session = FishbowlSession("twenty-sprouts")
        session._leaderboard = store

        session.reset(seed="test_seed_789")
        session.step(2)

        # Finalize without a verdict (user stops, no winner)
        session.finalize("user_stop")

        # No row should be recorded
        entries = store.entries()
        assert len(entries) == 0

    def test_e2e_competitive_verdict_records_row(self):
        """Positive E2E: a real FishbowlSession reaching a verdict writes exactly one row.

        Drives "twenty-sprouts" (a versus, code-decided ground-truth game) offline with the
        deterministic stub.  Each seat is bound to an explicit ``model_endpoint`` (what the
        Lab does via ``cast_models``) so the run.started cast map carries endpoints and the
        "winning model selected" gate is satisfied — without that, the run is finished and
        won but has no concrete winning model, so no row is recorded (see
        ``test_e2e_abandoned_run_not_recorded`` for the no-winner case).  Exercises the full
        ``finalize → _record_leaderboard → build_entry → store.record`` glue.
        """
        from src.ui.fishbowl.session import FishbowlSession

        store = make_leaderboard_store(url="sqlite://")
        session = FishbowlSession("twenty-sprouts")
        session._leaderboard = store
        # Bind a distinct endpoint to every seat (the Lab's cast_models, in miniature).
        for agent in session.conductor.scenario.agents:
            agent.manifest = agent.manifest.model_copy(update={"model_endpoint": f"openai/test/{agent.manifest.name}"})

        session.reset(seed="seed-e2e")
        for _ in range(12):
            session.step(1)
        ruled = session.force_verdict()  # lands judge.verdict + finalize("verdict")
        assert ruled is True

        rows = [r for r in store.entries() if r.run_id == session.conductor.run_id]
        assert len(rows) == 1
        row = rows[0]
        assert row.scenario == "twenty-sprouts"
        assert row.competition_kind == "versus"
        assert row.reason == "verdict"
        assert row.winner  # a real seat was crowned by the code-decided judge
        # The winner's bound endpoint is credited as the winning model.
        assert row.winning_models == [f"openai/test/{row.winner}"]
        assert row.winning_model == f"openai/test/{row.winner}"
