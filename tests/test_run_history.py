"""Run history & lifecycle: ``run.started`` enrichment, ``run.finished`` emission,
non-destructive reset, the run-scoped read surface, and multi-run indexing.

No mocks — the deterministic stub (conftest) and a couple of tiny in-process agents
drive everything, mirroring the idiom in ``test_conductor.py``.
"""

from __future__ import annotations

import pytest

from src.core.conductor import Conductor
from src.core.events import Event
from src.core.governor import BudgetExceeded, Governor
from src.core.ledger import Ledger
from src.core.manifest import AgentManifest, ScheduleConfig
from src.core.projections import rebuild_stage
from src.core.run_index import RunSummary, index_runs, index_runs_from_ledger
from src.core.sqlalchemy_ledger import SqlAlchemyLedger
from src.scenarios.base import Scenario
from src.ui.fishbowl.session import FishbowlSession


# ── tiny in-process agents ──────────────────────────────────────────────────────


class _JudgingAgent:
    """Rules a verdict every tick — stands in for the live Judge so a run resolves."""

    name = "judge"
    manifest = AgentManifest(
        name="judge",
        persona="p",
        may_emit=["judge.verdict"],
        schedule=ScheduleConfig(tick_every=1),
        model_endpoint="model://judge-endpoint",
        model_profile="strong",
    )

    def __init__(self) -> None:
        self.last_usage: dict = {}

    def act(self, run_id, turn, projection, recent_events) -> Event:
        return Event(
            run_id=run_id,
            turn=turn,
            kind="judge.verdict",
            actor="judge",
            payload={"text": "the verdict", "winner": "judge"},
        )


class _SpeakingAgent:
    name = "speaker"
    manifest = AgentManifest(
        name="speaker", persona="p", may_emit=["agent.spoke"], schedule=ScheduleConfig(tick_every=1)
    )

    def __init__(self) -> None:
        self.last_usage: dict = {}

    def act(self, run_id, turn, projection, recent_events) -> Event:
        return Event(run_id=run_id, turn=turn, kind="agent.spoke", actor="speaker", payload={"text": "hi"})


def _verdict_scenario() -> Scenario:
    return Scenario(name="verdict-world", default_seed="seed", agents=(_JudgingAgent(),))


# ── run.started enrichment ────────────────────────────────────────────────────────


class TestRunStartedPayload:
    def test_run_started_carries_cast_model_map_and_scenario(self):
        c = Conductor(scenario=_verdict_scenario())
        c.reset("seed")
        started = next(e for e in c.ledger.events_for_run(c.run_id) if e.kind == "run.started")
        assert started.payload["scenario"] == "verdict-world"
        cast = started.payload["cast"]
        assert cast["judge"] == {"model_endpoint": "model://judge-endpoint", "model_profile": "strong"}

    def test_run_started_reports_unbound_agent_without_manifest_fields(self):
        c = Conductor(scenario=Scenario(name="s", default_seed="seed", agents=(_SpeakingAgent(),)))
        c.reset("seed")
        started = next(e for e in c.ledger.events_for_run(c.run_id) if e.kind == "run.started")
        assert started.payload["cast"]["speaker"]["model_endpoint"] is None


# ── run.finished emission ─────────────────────────────────────────────────────────


class TestRunFinished:
    def test_finished_on_verdict_via_session_finalize(self):
        session = FishbowlSession("thousand-token-wood")
        session.reset()
        # Drive until the live Judge rules (the stub cast resolves quickly).
        for _ in range(session.autoplay_tick_cap):
            if session.has_verdict():
                break
            session.step()
        assert session.has_verdict(), "the stub cast should reach a verdict"

        session.finalize("verdict")
        finished = [
            e for e in session.conductor.ledger.events_for_run(session.conductor.run_id) if e.kind == "run.finished"
        ]
        assert len(finished) == 1
        assert finished[0].payload["reason"] == "verdict"

    def test_finished_on_budget_trip_carries_budget_reason(self):
        # A per-turn cap of 1 trips BudgetExceeded on the SECOND agent; the conductor
        # must close the run with reason "budget" before the stop propagates.
        scenario = Scenario(name="s", default_seed="seed", agents=(_SpeakingAgent(), _SpeakingAgent()))
        c = Conductor(scenario=scenario, governor=Governor(max_calls_per_turn=1))
        c.reset("seed")
        with pytest.raises(BudgetExceeded):
            c.step()
        finished = [e for e in c.ledger.events_for_run(c.run_id) if e.kind == "run.finished"]
        assert len(finished) == 1
        assert finished[0].payload["reason"] == "budget"

    def test_finalize_is_idempotent(self):
        c = Conductor(scenario=_verdict_scenario())
        c.reset("seed")
        first = c.finalize("user_stop")
        second = c.finalize("verdict")  # must NOT emit a second run.finished
        assert first is not None and second is not None
        assert first.id == second.id
        finished = [e for e in c.ledger.events_for_run(c.run_id) if e.kind == "run.finished"]
        assert len(finished) == 1
        assert finished[0].payload["reason"] == "user_stop"

    def test_versus_session_finalizes_with_team_winner(self):
        # End-to-end offline: the SpyHost stamps a team winner ("herd"/"spy") on the
        # verdict, and FishbowlSession.finalize must resolve winner_kind == "team"
        # (a team label, not a cast agent), never guessing a single winning_model.
        session = FishbowlSession("the-steeped")
        session.reset()
        for _ in range(session.autoplay_tick_cap):
            if session.has_verdict():
                break
            session.step()
        assert session.has_verdict(), "the stub spy-host should reach a verdict"

        session.finalize("verdict")
        finished = [
            e for e in session.conductor.ledger.events_for_run(session.conductor.run_id) if e.kind == "run.finished"
        ]
        assert len(finished) == 1
        payload = finished[0].payload
        assert payload["winner"] in ("herd", "spy")  # a team label, code-stamped
        assert payload["winner_kind"] == "team"
        assert payload["winning_model"] is None  # never guessed for a team win

    def test_session_finalize_derives_winner_and_model_from_verdict(self):
        scenario = _verdict_scenario()
        # Build a session-like conductor manually so we control the cast verdict.
        c = Conductor(scenario=scenario)
        c.reset("seed")
        c.step()  # judge rules: winner="judge"
        # Reproduce FishbowlSession.finalize's derivation against this conductor.
        run_events = c.ledger.events_for_run(c.run_id)
        verdict = next(e for e in reversed(run_events) if e.kind == "judge.verdict")
        winner = verdict.payload.get("winner")
        started = next(e for e in run_events if e.kind == "run.started")
        winning_model = (started.payload["cast"].get(winner) or {}).get("model_endpoint")
        finished = c.finalize("verdict", winner=winner, winning_model=winning_model)
        assert finished.payload["winner"] == "judge"
        assert finished.payload["winning_model"] == "model://judge-endpoint"


# ── non-destructive reset ─────────────────────────────────────────────────────────


class TestNonDestructiveReset:
    def test_reset_starts_new_run_and_keeps_prior_run_events(self):
        c = Conductor(scenario=_verdict_scenario())
        c.reset("seed-a")
        old_run = c.run_id
        c.step()
        old_events = c.ledger.events_for_run(old_run)
        assert old_events, "the first run produced events"

        c.reset("seed-b")
        new_run = c.run_id
        assert new_run != old_run, "reset mints a NEW run_id"
        # Prior run's events survive on the shared, append-only ledger.
        assert c.ledger.events_for_run(old_run) == old_events
        # The new run only sees its own run.started/genesis so far.
        new_events = c.ledger.events_for_run(new_run)
        assert new_events and all(e.run_id == new_run for e in new_events)
        assert any(e.kind == "run.started" for e in new_events)


# ── run-scoped read surface ───────────────────────────────────────────────────────


def _multi_run_ledger(ledger: Ledger) -> dict[str, list[Event]]:
    """Append two interleaved runs to *ledger*; return {run_id: events} oracle."""
    expected: dict[str, list[Event]] = {"run-A": [], "run-B": []}
    plan = [
        ("run-A", 0, "run.started"),
        ("run-B", 0, "run.started"),
        ("run-A", 1, "agent.spoke"),
        ("run-A", 1, "judge.verdict"),
        ("run-B", 1, "agent.spoke"),
        ("run-A", 2, "run.finished"),
    ]
    for run_id, turn, kind in plan:
        payload = {"seed": run_id} if kind == "run.started" else {"text": "t"}
        e = Event(run_id=run_id, turn=turn, kind=kind, actor="x", payload=payload)
        ledger.append(e)
        expected[run_id].append(e)
    return expected


class TestRunScopedReads:
    @pytest.mark.parametrize("ledger_factory", [Ledger, lambda: SqlAlchemyLedger("sqlite://")])
    def test_events_for_run_and_runs(self, ledger_factory):
        ledger = ledger_factory()
        expected = _multi_run_ledger(ledger)

        # runs() reports distinct run_ids in first-seen order.
        assert ledger.runs() == ("run-A", "run-B")

        for run_id, events in expected.items():
            got = ledger.events_for_run(run_id)
            assert [e.id for e in got] == [e.id for e in events]
            assert [e.kind for e in got] == [e.kind for e in events]

    def test_rebuild_stage_ignores_other_runs(self):
        ledger = Ledger()
        _multi_run_ledger(ledger)
        proj = rebuild_stage(ledger.events, "run-B")
        # run-B never emitted a verdict; run-A did — scoping must not bleed it in.
        assert proj.judge_notes == []

    def test_rebuild_stage_unscoped_sees_everything(self):
        ledger = Ledger()
        _multi_run_ledger(ledger)
        scoped = rebuild_stage(ledger.events, "run-B")
        unscoped = rebuild_stage(ledger.events)
        # The unscoped projection folds in run-A's verdict; the scoped one does not.
        assert unscoped.judge_notes != []
        assert scoped.judge_notes == []


# ── run index: oracle vs. SQL implementation ──────────────────────────────────────


def _index_oracle(events: tuple[Event, ...]) -> dict[str, list[str]]:
    """Reference per-run index: {run_id: [event_id, ...]} in first-seen/offset order."""
    index: dict[str, list[str]] = {}
    for e in events:
        index.setdefault(e.run_id, []).append(e.id)
    return index


class TestRunIndex:
    def test_sql_run_index_matches_oracle(self):
        sql = SqlAlchemyLedger("sqlite://")
        mem = Ledger()
        # Feed the SAME multi-run stream into a plain list to build the oracle.
        plan = [
            ("r1", "run.started"),
            ("r2", "run.started"),
            ("r1", "agent.spoke"),
            ("r3", "run.started"),
            ("r2", "agent.spoke"),
            ("r1", "run.finished"),
            ("r3", "agent.spoke"),
            ("r2", "run.finished"),
        ]
        stream: list[Event] = []
        for run_id, kind in plan:
            e = Event(run_id=run_id, turn=0, kind=kind, actor="x", payload={"text": "t"})
            sql.append(e)
            mem.append(e)
            stream.append(e)

        oracle = _index_oracle(tuple(stream))

        # runs() must agree with the oracle's first-seen run order.
        assert list(sql.runs()) == list(oracle.keys())
        assert list(mem.runs()) == list(oracle.keys())

        # Per-run event ordering must match the oracle on the SQL backend.
        for run_id, ids in oracle.items():
            assert [e.id for e in sql.events_for_run(run_id)] == ids
            assert [e.id for e in mem.events_for_run(run_id)] == ids


# ── run_index module: index_runs oracle vs. index_runs_from_ledger (SQL path) ─────


def _bookended_runs() -> list[Event]:
    """A two-run stream with full run.started/run.finished payloads, interleaved."""
    return [
        Event(
            run_id="r1",
            turn=0,
            kind="run.started",
            actor="conductor",
            payload={
                "seed": "acorn",
                "goal": "g",
                "scenario": "thousand_token_wood",
                "cast": {"judge": {"model_endpoint": "model://j", "model_profile": "strong"}},
            },
        ),
        Event(
            run_id="r2",
            turn=0,
            kind="run.started",
            actor="conductor",
            payload={"seed": "burrow", "scenario": "fishbowl", "cast": {}},
        ),
        Event(run_id="r1", turn=1, kind="agent.spoke", actor="judge", payload={"text": "t"}),
        Event(
            run_id="r1",
            turn=2,
            kind="run.finished",
            actor="conductor",
            payload={
                "reason": "verdict",
                "winner": "judge",
                "winner_kind": "agent",
                "winning_model": "model://j",
                "winning_models": ["model://j"],
                "turns": 2,
                "tokens": 1234,
            },
        ),
        # r2 starts but never finishes — its finished_* fields stay None/0.
        Event(run_id="r2", turn=1, kind="agent.spoke", actor="x", payload={"text": "t"}),
    ]


def _team_win_run() -> list[Event]:
    """A single versus run finishing on a TEAM win (winner_kind 'team', no single model)."""
    return [
        Event(
            run_id="v1",
            turn=0,
            kind="run.started",
            actor="conductor",
            payload={
                "seed": "leaf",
                "scenario": "the-steeped",
                "cast": {
                    "spy-cara": {"model_endpoint": "model://cara", "model_profile": "fast"},
                    "spy-bex": {"model_endpoint": "model://bex", "model_profile": "fast"},
                    "spy-ovo": {"model_endpoint": None, "model_profile": "fast"},
                },
            },
        ),
        Event(
            run_id="v1",
            turn=2,
            kind="run.finished",
            actor="conductor",
            payload={
                "reason": "verdict",
                "winner": "herd",
                "winner_kind": "team",
                "winning_model": None,  # never guessed for a team win
                "winning_models": ["model://cara", "model://bex"],  # None member endpoint dropped
                "turns": 2,
                "tokens": 50,
            },
        ),
    ]


class TestRunIndexModule:
    def test_index_runs_folds_bookend_events(self):
        summaries = index_runs(_bookended_runs())
        assert [s.run_id for s in summaries] == ["r1", "r2"]  # first-seen order

        r1, r2 = summaries
        assert isinstance(r1, RunSummary)
        assert (r1.scenario, r1.seed) == ("thousand_token_wood", "acorn")
        assert r1.cast["judge"].model_endpoint == "model://j"
        assert r1.cast["judge"].model_profile == "strong"
        assert (r1.reason, r1.winner, r1.winning_model) == ("verdict", "judge", "model://j")
        # ADR-0029 attribution keys round-trip through the projection (agent win).
        assert r1.winner_kind == "agent"
        assert r1.winning_models == ["model://j"]
        assert (r1.turns, r1.tokens) == (2, 1234)
        assert r1.started_at is not None and r1.finished_at is not None

        # r2 started but never finished — terminal fields stay at their defaults.
        assert (r2.scenario, r2.seed) == ("fishbowl", "burrow")
        assert r2.reason is None and r2.winner is None
        assert (r2.turns, r2.tokens) == (0, 0)
        assert r2.finished_at is None
        # An unfinished run carries the additive ADR-0029 defaults, never a stale guess.
        assert r2.winner_kind is None
        assert r2.winning_models == []

    def test_index_runs_round_trips_a_team_win(self):
        # winner_kind 'team' carries no single winning_model, only the member endpoints —
        # and a None member endpoint is dropped from winning_models.
        (summary,) = index_runs(_team_win_run())
        assert summary.winner == "herd"
        assert summary.winner_kind == "team"
        assert summary.winning_model is None
        assert summary.winning_models == ["model://cara", "model://bex"]

    @pytest.mark.parametrize("make_ledger", [Ledger, lambda: SqlAlchemyLedger("sqlite://")])
    @pytest.mark.parametrize("events_factory", [_bookended_runs, _team_win_run])
    def test_from_ledger_matches_pure_oracle(self, make_ledger, events_factory):
        events = events_factory()
        ledger = make_ledger()
        for e in events:
            ledger.append(e)
        # The indexed-query path must produce exactly what the pure oracle does —
        # including the additive ADR-0029 attribution keys (agent win and team win).
        assert index_runs_from_ledger(ledger) == index_runs(events)
