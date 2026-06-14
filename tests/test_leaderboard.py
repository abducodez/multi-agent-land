"""Leaderboard aggregations over LeaderboardEntry rows — zero-mock, deterministic.

This suite verifies the leaderboard's read-model invariants: that aggregations over a
list of LeaderboardEntry objects produce correct tables, win_rate math, sorting, and that
projections are deterministic regardless of input order.

Test strategy: Build LeaderboardEntry objects directly (no events, no ledger), call the
public aggregation functions, and assert on the resulting rows. This guards against:

  - Incorrect model endpoint deduplication (one play per endpoint per run, even when
    filling multiple seats).
  - Team wins crediting every member's model vs single-agent wins.
  - Judges and other unmapped cast members affecting fairness and agent tables.
  - Sorting regressions (determinism is a contract).
  - Headline generation requiring symmetric seats + ≥2 models with ≥1 win each.
  - Edge cases: empty input, zero plays, missing fields, out-of-order entries.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.core.leaderboard import (
    agent_table,
    fairness_table,
    headline,
    model_table,
    scenario_sessions,
)
from src.core.leaderboard_store import LeaderboardEntry
from src.core.run_index import CastBinding


# ── LeaderboardEntry builders ──────────────────────────────────────────────────────


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
        finished_at = started_at + timedelta(minutes=5)
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


# ── Tests: scenario_sessions (newest-first filtering) ─────────────────────────────


class TestScenarioSessions:
    """Verify scenario_sessions filters, orders, and projects correctly."""

    def test_empty_entries_returns_empty_list(self):
        """Empty entry list returns no sessions."""
        result = scenario_sessions([], "Debate Duel")
        assert result == []

    def test_no_winner_entry_excluded(self):
        """An entry with no winner is dropped (defensive gate)."""
        entry = _entry(winner=None)
        result = scenario_sessions([entry], "Debate Duel")
        assert result == []

    def test_scenario_filter_excludes_other_scenarios(self):
        """Sessions from other scenarios are not returned."""
        entry = _entry(scenario="Debate Duel")
        result = scenario_sessions([entry], "Trivia Night")
        assert result == []

    def test_one_entry_has_all_fields(self):
        """A single entry is projected with all fields intact."""
        cast = {
            "alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B", model_profile="large"),
            "bob": CastBinding(model_endpoint="google/gemma-12B", model_profile="medium"),
        }
        started_at = datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc)
        finished_at = started_at + timedelta(minutes=5)
        entry = _entry(
            run_id="r1",
            scenario="Debate Duel",
            seed="abc123",
            cast=cast,
            symmetric_seats=["debater_a", "debater_b"],
            winner="alice",
            winner_kind="agent",
            winning_model="openai/openbmb/MiniCPM-8B",
            winning_models=["openai/openbmb/MiniCPM-8B"],
            turns=7,
            tokens=320,
            started_at=started_at,
            finished_at=finished_at,
        )
        result = scenario_sessions([entry], "Debate Duel")
        assert len(result) == 1
        row = result[0]
        assert row.run_id == "r1"
        assert row.scenario == "Debate Duel"
        assert row.seed == "abc123"
        assert row.winner == "alice"
        assert row.winner_kind == "agent"
        assert row.turns == 7
        assert row.tokens == 320
        assert row.started_at == started_at
        assert row.finished_at == finished_at
        assert "alice" in row.cast
        assert row.cast["alice"].model_endpoint == "openai/openbmb/MiniCPM-8B"

    def test_newest_first_order(self):
        """Sessions are sorted newest first by finished_at."""
        old = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        new = datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc)
        entries = [
            _entry(run_id="r1", finished_at=old + timedelta(minutes=1)),
            _entry(run_id="r2", finished_at=new + timedelta(minutes=1)),
        ]
        result = scenario_sessions(entries, "Debate Duel")
        assert len(result) == 2
        assert result[0].run_id == "r2"  # newer first
        assert result[1].run_id == "r1"

    def test_run_id_tiebreak_when_finished_at_same(self):
        """When finished_at is equal, run_id is the tiebreaker (ascending alphabetical)."""
        same_time = datetime(2025, 6, 14, 10, 0, 0, tzinfo=timezone.utc)
        entries = [
            _entry(run_id="r2", finished_at=same_time + timedelta(minutes=1)),
            _entry(run_id="r1", finished_at=same_time + timedelta(minutes=1)),
        ]
        result = scenario_sessions(entries, "Debate Duel")
        assert len(result) == 2
        # Same finished_at; run_id breaks ties in ascending order (r1 < r2)
        assert result[0].run_id == "r1"
        assert result[1].run_id == "r2"


# ── Tests: model_table (endpoint-level stats across all scenarios) ──────────────────


class TestModelTable:
    """Verify model aggregation: plays, wins, win_rate, scenarios, deterministic sort."""

    def test_empty_entries_returns_empty_list(self):
        """No entries → no rows."""
        result = model_table([])
        assert result == []

    def test_no_winner_entry_not_counted(self):
        """An entry with no winner contributes no stats."""
        entry = _entry(winner=None)
        result = model_table([entry])
        assert result == []

    def test_one_model_one_play_one_win(self):
        """A single-entry win: plays=1, wins=1, win_rate=1.0."""
        entry = _entry(
            scenario="Debate Duel",
            cast={"alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B")},
            winner="alice",
            winning_model="openai/openbmb/MiniCPM-8B",
            winning_models=["openai/openbmb/MiniCPM-8B"],
        )
        result = model_table([entry])
        assert len(result) == 1
        assert result[0].model == "openai/openbmb/MiniCPM-8B"
        assert result[0].plays == 1
        assert result[0].wins == 1
        assert result[0].win_rate == 1.0
        assert result[0].scenarios == ["Debate Duel"]

    def test_one_model_mixed_wins_and_losses(self):
        """A model with 2 wins / 3 plays has win_rate ≈ 0.667."""
        entries = [
            _entry(
                run_id="r1",
                scenario="Debate Duel",
                cast={"alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B")},
                winner="alice",
                winning_model="openai/openbmb/MiniCPM-8B",
            ),
            _entry(
                run_id="r2",
                scenario="Debate Duel",
                cast={"alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B")},
                winner="alice",
                winning_model="openai/openbmb/MiniCPM-8B",
            ),
            _entry(
                run_id="r3",
                scenario="Debate Duel",
                cast={
                    "alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "bob": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="bob",
                winning_model="google/gemma-12B",
            ),
        ]
        result = model_table(entries)
        m8b = next((r for r in result if r.model == "openai/openbmb/MiniCPM-8B"), None)
        assert m8b is not None
        assert m8b.plays == 3
        assert m8b.wins == 2
        assert abs(m8b.win_rate - (2 / 3)) < 0.001

    def test_model_endpoint_deduplication_one_play_per_endpoint_per_run(self):
        """A model filling two seats in one run counts as one play (dedup by endpoint)."""
        entry = _entry(
            run_id="r1",
            scenario="Some Scenario",
            cast={
                "alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                "bob": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),  # same endpoint
            },
            winner="alice",
            winning_model="openai/openbmb/MiniCPM-8B",
            symmetric_seats=["seat_a", "seat_b"],
        )
        result = model_table([entry])
        assert len(result) == 1
        assert result[0].plays == 1  # not 2!
        assert result[0].wins == 1

    def test_scenarios_lists_all_distinct_scenarios_sorted(self):
        """A model in multiple scenarios lists all of them (sorted)."""
        entries = [
            _entry(
                run_id="r1",
                scenario="Zebra Debate",
                cast={"alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B")},
                winner="alice",
                winning_model="openai/openbmb/MiniCPM-8B",
            ),
            _entry(
                run_id="r2",
                scenario="Alpha Trivia",
                cast={"alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B")},
                winner="alice",
                winning_model="openai/openbmb/MiniCPM-8B",
            ),
        ]
        result = model_table(entries)
        assert len(result) == 1
        assert result[0].scenarios == ["Alpha Trivia", "Zebra Debate"]

    def test_deterministic_sort_win_rate_then_wins_then_plays_then_model(self):
        """model_table sorts by (-win_rate, -wins, -plays, model asc)."""
        entries = [
            # MiniCPM-8B: 2 wins / 2 plays = 1.0
            _entry(
                run_id="r1",
                cast={"alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B")},
                winner="alice",
                winning_model="openai/openbmb/MiniCPM-8B",
            ),
            _entry(
                run_id="r2",
                cast={"alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B")},
                winner="alice",
                winning_model="openai/openbmb/MiniCPM-8B",
            ),
            # Gemma: 1 win / 2 plays = 0.5
            _entry(
                run_id="r3",
                cast={"alice": CastBinding(model_endpoint="google/gemma-12B")},
                winner="alice",
                winning_model="google/gemma-12B",
            ),
            _entry(
                run_id="r4",
                cast={
                    "alice": CastBinding(model_endpoint="google/gemma-12B"),
                    "bob": CastBinding(model_endpoint="openai/openbmb/MiniCPM-4B"),
                },
                winner="bob",
                winning_model="openai/openbmb/MiniCPM-4B",  # MiniCPM-4B wins here
            ),
            # MiniCPM-4B: 0 wins / 2 plays = 0.0 (plays in r4 winning, but that's a different calc)
            _entry(
                run_id="r5",
                cast={
                    "alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-4B"),
                    "bob": CastBinding(model_endpoint="meta/llama-7B"),
                },
                winner="bob",
                winning_model="meta/llama-7B",
            ),
            _entry(
                run_id="r6",
                cast={
                    "alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-4B"),
                    "bob": CastBinding(model_endpoint="meta/llama-7B"),
                },
                winner="alice",  # MiniCPM-4B wins (but alice is the seat, need to check...)
                winning_model="openai/openbmb/MiniCPM-4B",
            ),
        ]
        result = model_table(entries)
        result_models = [r.model for r in result]
        assert result_models[0] == "openai/openbmb/MiniCPM-8B"  # 1.0, 2 wins, 2 plays
        # MiniCPM-4B: 2 wins / 3 plays ≈ 0.67, Gemma: 1 win / 2 plays = 0.5, Llama: 1 win / 2 plays = 0.5
        # Sort: MiniCPM-8B (1.0) > MiniCPM-4B (0.67) > Gemma (0.5, "gemma" < "llama") > Llama (0.5)
        assert result_models[1] == "openai/openbmb/MiniCPM-4B"  # 0.67, 2 wins
        assert result_models[2] == "google/gemma-12B"  # 0.5, 1 win
        assert result_models[3] == "meta/llama-7B"  # 0.5, 1 win

    def test_winning_models_includes_all_credited_endpoints(self):
        """A run with winning_models=[a,b] credits both with a win."""
        entry = _entry(
            run_id="r1",
            scenario="Debate Duel",
            cast={
                "alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                "bob": CastBinding(model_endpoint="google/gemma-12B"),
            },
            winner="team_a",
            winner_kind="team",
            winning_model=None,
            winning_models=["openai/openbmb/MiniCPM-8B", "google/gemma-12B"],
            symmetric_seats=["seat_a", "seat_b"],
        )
        result = model_table([entry])
        assert len(result) == 2
        m8b = next((r for r in result if r.model == "openai/openbmb/MiniCPM-8B"), None)
        assert m8b is not None and m8b.wins == 1
        gemma = next((r for r in result if r.model == "google/gemma-12B"), None)
        assert gemma is not None and gemma.wins == 1


# ── Tests: agent_table (per-scenario, per-persona stats) ────────────────────────────


class TestAgentTable:
    """Verify agent attribution: plays, wins, seat_type, model_endpoints, sort."""

    def test_empty_entries_returns_empty_list(self):
        """No entries → no rows."""
        result = agent_table([], "Debate Duel")
        assert result == []

    def test_agent_filtered_by_scenario(self):
        """agent_table only includes agents from the named scenario."""
        entries = [
            _entry(
                run_id="r1",
                scenario="Debate Duel",
                cast={"alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B")},
                winner="alice",
            ),
            _entry(
                run_id="r2",
                scenario="Other Scenario",
                cast={"bob": CastBinding(model_endpoint="google/gemma-12B")},
                winner="bob",
            ),
        ]
        result = agent_table(entries, "Debate Duel")
        agents = [r.agent for r in result]
        assert agents == ["alice"]

    def test_agent_single_play_single_win(self):
        """An agent with one win: plays=1, wins=1, win_rate=1.0."""
        entry = _entry(
            scenario="Debate Duel",
            cast={"alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B")},
            winner="alice",
            winning_model="openai/openbmb/MiniCPM-8B",
            symmetric_seats=["debater_a"],
        )
        result = agent_table([entry], "Debate Duel")
        assert len(result) == 1
        assert result[0].agent == "alice"
        assert result[0].plays == 1
        assert result[0].wins == 1
        assert result[0].win_rate == 1.0

    def test_agent_seat_type_from_symmetric_seats(self):
        """In symmetric-seat scenarios, agent's seat_type is the seat name."""
        entry = _entry(
            scenario="Debate Duel",
            cast={"debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B")},
            winner="debater_a",
            winning_model="openai/openbmb/MiniCPM-8B",
            symmetric_seats=["debater_a", "debater_b"],
        )
        result = agent_table([entry], "Debate Duel")
        assert result[0].agent == "debater_a"
        assert result[0].seat_type == "debater_a"

    def test_agent_seat_type_from_teams(self):
        """In team scenarios, agent's seat_type is the team label they belong to."""
        entry = _entry(
            scenario="Team Game",
            cast={
                "alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                "bob": CastBinding(model_endpoint="google/gemma-12B"),
            },
            teams={"team_a": ["alice", "charlie"], "team_b": ["bob"]},
            winner="alice",
            winning_model="openai/openbmb/MiniCPM-8B",
            winner_kind="agent",
            competition_kind="versus",
        )
        result = agent_table([entry], "Team Game")
        alice = next((r for r in result if r.agent == "alice"), None)
        bob = next((r for r in result if r.agent == "bob"), None)
        assert alice is not None and alice.seat_type == "team_a"
        assert bob is not None and bob.seat_type == "team_b"

    def test_agent_seat_type_empty_when_unmapped(self):
        """An agent not in teams or symmetric_seats (e.g., judge) has seat_type=''."""
        entry = _entry(
            scenario="Debate Duel",
            cast={
                "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                "judge": CastBinding(model_endpoint=None),
            },
            winner="debater_a",
            winning_model="openai/openbmb/MiniCPM-8B",
            symmetric_seats=["debater_a"],
            competition_kind="judged",
        )
        result = agent_table([entry], "Debate Duel")
        judge_row = next((r for r in result if r.agent == "judge"), None)
        assert judge_row is not None
        assert judge_row.seat_type == ""

    def test_agent_model_endpoints_deduped_sorted(self):
        """An agent's model_endpoints is sorted, distinct models that filled the seat."""
        entries = [
            _entry(
                run_id="r1",
                scenario="Debate Duel",
                cast={"alice": CastBinding(model_endpoint="google/gemma-12B")},
                winner="alice",
                winning_model="google/gemma-12B",
            ),
            _entry(
                run_id="r2",
                scenario="Debate Duel",
                cast={"alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B")},
                winner="alice",
                winning_model="openai/openbmb/MiniCPM-8B",
            ),
            _entry(
                run_id="r3",
                scenario="Debate Duel",
                cast={"alice": CastBinding(model_endpoint="google/gemma-12B")},  # repeat
                winner="alice",
                winning_model="google/gemma-12B",
            ),
        ]
        result = agent_table(entries, "Debate Duel")
        assert len(result) == 1
        assert result[0].model_endpoints == ["google/gemma-12B", "openai/openbmb/MiniCPM-8B"]

    def test_team_win_credits_all_team_members(self):
        """A team win credits every member of the winning team with a win."""
        entry = _entry(
            scenario="Team Game",
            cast={
                "alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                "bob": CastBinding(model_endpoint="google/gemma-12B"),
            },
            teams={"team_a": ["alice"], "team_b": ["bob"]},
            winner="team_a",
            winner_kind="team",
            winning_models=["openai/openbmb/MiniCPM-8B"],
            winning_model=None,
            competition_kind="versus",
        )
        result = agent_table([entry], "Team Game")
        alice = next((r for r in result if r.agent == "alice"), None)
        bob = next((r for r in result if r.agent == "bob"), None)
        assert alice is not None and alice.wins == 1
        assert bob is not None and bob.wins == 0


# ── Tests: fairness_table (seat-type aggregation) ─────────────────────────────────


class TestFairnessTable:
    """Verify fairness_table: seat-type aggregation, unmapped excluded, sort."""

    def test_empty_entries_returns_empty_list(self):
        """No entries → no rows."""
        result = fairness_table([], "Debate Duel")
        assert result == []

    def test_only_declared_seat_types_appear(self):
        """Only declared seat_types (teams or symmetric_seats) appear."""
        entry = _entry(
            scenario="Debate Duel",
            cast={
                "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                "judge": CastBinding(model_endpoint=None),
            },
            winner="debater_a",
            winning_model="openai/openbmb/MiniCPM-8B",
            symmetric_seats=["debater_a"],
            competition_kind="judged",
        )
        result = fairness_table([entry], "Debate Duel")
        seat_types = [r.seat_type for r in result]
        # Only "debater_a" is declared; "judge" is unmapped.
        assert seat_types == ["debater_a"]

    def test_symmetric_seat_plays_and_wins(self):
        """In symmetric-seat scenarios, each seat contributes one play per run."""
        entry = _entry(
            scenario="Debate Duel",
            cast={
                "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                "debater_b": CastBinding(model_endpoint="google/gemma-12B"),
            },
            winner="debater_a",
            winning_model="openai/openbmb/MiniCPM-8B",
            symmetric_seats=["debater_a", "debater_b"],
        )
        result = fairness_table([entry], "Debate Duel")
        debater_a = next((r for r in result if r.seat_type == "debater_a"), None)
        debater_b = next((r for r in result if r.seat_type == "debater_b"), None)
        assert debater_a is not None and debater_a.plays == 1 and debater_a.wins == 1
        assert debater_b is not None and debater_b.plays == 1 and debater_b.wins == 0

    def test_team_seats_aggregation(self):
        """In team scenarios, each team contributes one play; wins go to winning team."""
        entry = _entry(
            scenario="Team Game",
            cast={
                "alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                "bob": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                "charlie": CastBinding(model_endpoint="google/gemma-12B"),
            },
            teams={"team_a": ["alice", "bob"], "team_b": ["charlie"]},
            winner="team_a",
            winner_kind="team",
            winning_models=["openai/openbmb/MiniCPM-8B"],
            winning_model=None,
            competition_kind="versus",
        )
        result = fairness_table([entry], "Team Game")
        team_a = next((r for r in result if r.seat_type == "team_a"), None)
        team_b = next((r for r in result if r.seat_type == "team_b"), None)
        assert team_a is not None and team_a.plays == 1 and team_a.wins == 1
        assert team_b is not None and team_b.plays == 1 and team_b.wins == 0

    def test_judge_not_counted_in_fairness(self):
        """A judge (unmapped cast member) does not appear in fairness_table."""
        entry = _entry(
            scenario="Debate Duel",
            cast={
                "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                "judge": CastBinding(model_endpoint=None),
            },
            winner="debater_a",
            winning_model="openai/openbmb/MiniCPM-8B",
            symmetric_seats=["debater_a"],
            competition_kind="judged",
        )
        result = fairness_table([entry], "Debate Duel")
        assert len(result) == 1
        assert result[0].seat_type == "debater_a"

    def test_win_rate_calculated_per_seat_type(self):
        """A seat winning 1 of 2 runs has win_rate=0.5."""
        entries = [
            _entry(
                run_id="r1",
                scenario="Debate Duel",
                cast={
                    "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "debater_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="debater_a",
                winning_model="openai/openbmb/MiniCPM-8B",
                symmetric_seats=["debater_a", "debater_b"],
            ),
            _entry(
                run_id="r2",
                scenario="Debate Duel",
                cast={
                    "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "debater_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="debater_b",
                winning_model="google/gemma-12B",
                symmetric_seats=["debater_a", "debater_b"],
            ),
        ]
        result = fairness_table(entries, "Debate Duel")
        debater_a = next((r for r in result if r.seat_type == "debater_a"), None)
        debater_b = next((r for r in result if r.seat_type == "debater_b"), None)
        assert debater_a is not None and abs(debater_a.win_rate - 0.5) < 0.001
        assert debater_b is not None and abs(debater_b.win_rate - 0.5) < 0.001


# ── Tests: headline (model-vs-model narrative) ─────────────────────────────────────


class TestHeadline:
    """Verify headline: symmetric seats + ≥2 models with ≥1 win each."""

    def test_empty_entries_returns_none(self):
        """No entries → no headline."""
        result = headline([])
        assert result is None

    def test_no_symmetric_seat_scenarios_returns_none(self):
        """Headline needs symmetric-seat scenario (model-vs-model); team scenarios excluded."""
        entry = _entry(
            scenario="Team Game",
            cast={
                "alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                "bob": CastBinding(model_endpoint="google/gemma-12B"),
            },
            teams={"team_a": ["alice"], "team_b": ["bob"]},
            winner="team_a",
            winner_kind="team",
            winning_models=["openai/openbmb/MiniCPM-8B"],
            winning_model=None,
            competition_kind="versus",
        )
        result = headline([entry])
        assert result is None

    def test_only_one_model_in_scenario_returns_none(self):
        """A scenario with only one model (even if winning) doesn't qualify."""
        entry = _entry(
            scenario="Debate Duel",
            cast={
                "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                "debater_b": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
            },
            winner="debater_a",
            winning_model="openai/openbmb/MiniCPM-8B",
            symmetric_seats=["debater_a", "debater_b"],
        )
        result = headline([entry])
        assert result is None  # only 1 distinct model

    def test_two_models_but_loser_has_zero_wins_returns_none(self):
        """Headline needs ≥2 models that have each won ≥1."""
        entry = _entry(
            scenario="Debate Duel",
            cast={
                "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                "debater_b": CastBinding(model_endpoint="google/gemma-12B"),
            },
            winner="debater_a",
            winning_model="openai/openbmb/MiniCPM-8B",
            winning_models=["openai/openbmb/MiniCPM-8B"],
            symmetric_seats=["debater_a", "debater_b"],
        )
        result = headline([entry])
        assert result is None  # gemma has 0 wins

    def test_headline_with_two_models_both_winning(self):
        """Headline is generated when ≥2 models have each won ≥1 in symmetric scenario."""
        entries = [
            _entry(
                run_id="r1",
                scenario="Debate Duel",
                cast={
                    "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "debater_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="debater_a",
                winning_model="openai/openbmb/MiniCPM-8B",
                winning_models=["openai/openbmb/MiniCPM-8B"],
                symmetric_seats=["debater_a", "debater_b"],
            ),
            _entry(
                run_id="r2",
                scenario="Debate Duel",
                cast={
                    "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "debater_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="debater_b",
                winning_model="google/gemma-12B",
                winning_models=["google/gemma-12B"],
                symmetric_seats=["debater_a", "debater_b"],
            ),
        ]
        result = headline(entries)
        assert result is not None
        assert "MiniCPM" in result
        assert "gemma" in result.lower()
        assert "Debate Duel" in result
        assert "1-1" in result

    def test_headline_picks_most_played_scenario(self):
        """When multiple scenarios qualify, headline picks the one with most games decided."""
        entries = [
            # Debate Duel: 1 game
            _entry(
                run_id="r1",
                scenario="Debate Duel",
                cast={
                    "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "debater_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="debater_a",
                winning_model="openai/openbmb/MiniCPM-8B",
                symmetric_seats=["debater_a", "debater_b"],
            ),
            # Trivia Night: 3 games (most)
            _entry(
                run_id="r2",
                scenario="Trivia Night",
                cast={
                    "player_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "player_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="player_a",
                winning_model="openai/openbmb/MiniCPM-8B",
                symmetric_seats=["player_a", "player_b"],
            ),
            _entry(
                run_id="r3",
                scenario="Trivia Night",
                cast={
                    "player_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "player_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="player_b",
                winning_model="google/gemma-12B",
                symmetric_seats=["player_a", "player_b"],
            ),
            _entry(
                run_id="r4",
                scenario="Trivia Night",
                cast={
                    "player_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "player_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="player_b",
                winning_model="google/gemma-12B",
                symmetric_seats=["player_a", "player_b"],
            ),
        ]
        result = headline(entries)
        assert result is not None
        assert "Trivia Night" in result

    def test_headline_alphabetical_tiebreak_when_same_wins(self):
        """When scenarios tie on wins, pick the alphabetically-first scenario name."""
        entries = [
            # "Aardvark": 1 win for MiniCPM, 1 for Gemma
            _entry(
                run_id="a1",
                scenario="Aardvark Duel",
                cast={
                    "p1": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "p2": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="p1",
                winning_model="openai/openbmb/MiniCPM-8B",
                symmetric_seats=["p1", "p2"],
            ),
            _entry(
                run_id="a2",
                scenario="Aardvark Duel",
                cast={
                    "p1": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "p2": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="p2",
                winning_model="google/gemma-12B",
                symmetric_seats=["p1", "p2"],
            ),
            # "Zebra": same record, but alphabetically later
            _entry(
                run_id="z1",
                scenario="Zebra Duel",
                cast={
                    "p1": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "p2": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="p1",
                winning_model="openai/openbmb/MiniCPM-8B",
                symmetric_seats=["p1", "p2"],
            ),
            _entry(
                run_id="z2",
                scenario="Zebra Duel",
                cast={
                    "p1": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "p2": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="p2",
                winning_model="google/gemma-12B",
                symmetric_seats=["p1", "p2"],
            ),
        ]
        result = headline(entries)
        assert result is not None
        assert "Aardvark Duel" in result  # alphabetically first

    def test_headline_format(self):
        """Headline format: 'ModelA beats ModelB · X-Y at Scenario'."""
        entries = [
            _entry(
                run_id="r1",
                scenario="Debate Duel",
                cast={
                    "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "debater_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="debater_a",
                winning_model="openai/openbmb/MiniCPM-8B",
                symmetric_seats=["debater_a", "debater_b"],
            ),
            _entry(
                run_id="r2",
                scenario="Debate Duel",
                cast={
                    "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "debater_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="debater_a",
                winning_model="openai/openbmb/MiniCPM-8B",
                symmetric_seats=["debater_a", "debater_b"],
            ),
            _entry(
                run_id="r3",
                scenario="Debate Duel",
                cast={
                    "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "debater_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="debater_b",
                winning_model="google/gemma-12B",
                symmetric_seats=["debater_a", "debater_b"],
            ),
        ]
        result = headline(entries)
        assert result is not None
        assert "beats" in result
        assert "·" in result
        assert "Debate Duel" in result
        assert "2-1" in result  # MiniCPM beats Gemma 2-1


# ── Tests: Purity (determinism and idempotency) ────────────────────────────────────


class TestProjectionPurity:
    """Verify that projections are deterministic regardless of entry order."""

    def test_scenario_sessions_idempotent(self):
        """Calling scenario_sessions twice returns the same result."""
        entry = _entry(scenario="Debate Duel")
        result1 = scenario_sessions([entry], "Debate Duel")
        result2 = scenario_sessions([entry], "Debate Duel")
        assert result1 == result2

    def test_model_table_idempotent(self):
        """Calling model_table twice returns the same result."""
        entry = _entry()
        result1 = model_table([entry])
        result2 = model_table([entry])
        assert result1 == result2

    def test_agent_table_deterministic_order_independent(self):
        """agent_table produces the same result regardless of entry order."""
        entries = [
            _entry(
                run_id="r1",
                scenario="Debate Duel",
                cast={"alice": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B")},
                winner="alice",
            ),
            _entry(
                run_id="r2",
                scenario="Debate Duel",
                cast={"bob": CastBinding(model_endpoint="google/gemma-12B")},
                winner="bob",
            ),
        ]
        result1 = agent_table(entries, "Debate Duel")
        result2 = agent_table(list(reversed(entries)), "Debate Duel")
        assert result1 == result2

    def test_fairness_table_deterministic_order_independent(self):
        """fairness_table produces the same result regardless of entry order."""
        entries = [
            _entry(
                run_id="r1",
                scenario="Debate Duel",
                cast={
                    "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "debater_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="debater_a",
                symmetric_seats=["debater_a", "debater_b"],
            ),
            _entry(
                run_id="r2",
                scenario="Debate Duel",
                cast={
                    "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "debater_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="debater_b",
                symmetric_seats=["debater_a", "debater_b"],
            ),
        ]
        result1 = fairness_table(entries, "Debate Duel")
        result2 = fairness_table(list(reversed(entries)), "Debate Duel")
        assert result1 == result2

    def test_headline_deterministic_order_independent(self):
        """headline produces the same result regardless of entry order."""
        entries = [
            _entry(
                run_id="r1",
                scenario="Debate Duel",
                cast={
                    "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "debater_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="debater_a",
                symmetric_seats=["debater_a", "debater_b"],
            ),
            _entry(
                run_id="r2",
                scenario="Debate Duel",
                cast={
                    "debater_a": CastBinding(model_endpoint="openai/openbmb/MiniCPM-8B"),
                    "debater_b": CastBinding(model_endpoint="google/gemma-12B"),
                },
                winner="debater_b",
                symmetric_seats=["debater_a", "debater_b"],
            ),
        ]
        result1 = headline(entries)
        result2 = headline(list(reversed(entries)))
        assert result1 == result2
