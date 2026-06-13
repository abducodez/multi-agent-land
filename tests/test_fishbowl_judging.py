"""The curtain call — "Start judging" / limit-reached forced verdict (no mocks, offline stub).

When the visitor presses *Start judging* (or a budget/turn limit ends the cast's run), the
show stops the cast mid-flight and hands the floor to the judge, which reads the whole run
ledger and lands a ``judge.verdict`` carrying a ``winner``.  These tests pin that contract on
real offline sessions:

  1. ``Conductor.force_verdict`` lands a verdict + winner — *even when the budget is spent*;
  2. it silences the cast (drains the pending/trigger queues) and is idempotent;
  3. a cast with no judge can't be forced (returns ``None`` / ``False``), never crashing;
  4. ``FishbowlSession.force_verdict`` closes the run (``run.finished`` reason ``verdict``)
     and the snapshot surfaces the ruling.
"""

from __future__ import annotations

from src.core.governor import Governor
from src.ui.fishbowl.session import FishbowlSession

# A judged duel (has a judge) and a no-judge grove — the two halves of the contract.
_JUDGED = "debate-duel"
_NO_JUDGE = "oracle-grove"


def _warm(scenario: str, *, governor: Governor | None = None, ticks: int = 6) -> FishbowlSession:
    """A real session stepped a few times so there's a discussion for the judge to read."""
    session = FishbowlSession(scenario)
    if governor is not None:
        session.conductor.governor = governor
    session.reset()
    for _ in range(ticks):
        if session.has_verdict():
            break
        session.step_one()
    return session


# ── Conductor.force_verdict ──────────────────────────────────────────────────────


def test_force_verdict_lands_a_verdict_with_a_winner() -> None:
    session = _warm(_JUDGED)
    assert not session.has_verdict()  # the judge hasn't ruled on its own yet
    verdict = session.conductor.force_verdict()
    assert verdict is not None
    assert verdict.kind == "judge.verdict"
    # The competition handler fills an offline winner deterministically (ADR-0029).
    assert verdict.payload.get("winner")


def test_force_verdict_rules_even_when_the_budget_is_spent() -> None:
    # The whole point of the curtain call: a *limit* triggers it, so the judge must rule
    # un-gated even though the governor that ended the show is already exhausted.
    session = _warm(_JUDGED)
    session.conductor.governor.max_total_calls = 0  # any further gated act would trip
    verdict = session.conductor.force_verdict()
    assert verdict is not None and verdict.kind == "judge.verdict"


def test_force_verdict_silences_the_cast() -> None:
    session = _warm(_JUDGED)
    # Seed pending/trigger work so we can prove the curtain call drains it.
    session.conductor._pending.append(session.conductor.scenario.agents[0])
    session.conductor.force_verdict()
    assert not session.conductor._pending
    assert not session.conductor._trigger_queue


def test_force_verdict_is_idempotent() -> None:
    session = _warm(_JUDGED)
    first = session.conductor.force_verdict()
    verdicts_after_first = [e for e in session.events if e.kind == "judge.verdict"]
    second = session.conductor.force_verdict()
    verdicts_after_second = [e for e in session.events if e.kind == "judge.verdict"]
    assert first is not None and second is not None
    assert second.id == first.id  # same ruling returned, never re-judged
    assert len(verdicts_after_first) == len(verdicts_after_second) == 1  # no duplicate


def test_force_verdict_returns_none_without_a_judge() -> None:
    session = _warm(_NO_JUDGE)
    assert session.conductor.force_verdict() is None
    assert not session.has_verdict()  # nothing was fabricated


# ── FishbowlSession surface ────────────────────────────────────────────────────────


def test_has_judge_reflects_the_cast() -> None:
    assert _warm(_JUDGED, ticks=0).has_judge() is True
    assert _warm(_NO_JUDGE, ticks=0).has_judge() is False


def test_session_force_verdict_closes_the_run_with_the_winner() -> None:
    session = _warm(_JUDGED)
    assert session.force_verdict() is True
    assert session.has_verdict()
    # The run is closed and self-describing: run.finished, reason 'verdict', a named winner.
    finished = [e for e in session.events if e.kind == "run.finished"]
    assert len(finished) == 1
    assert finished[0].payload.get("reason") == "verdict"
    assert finished[0].payload.get("winner")
    # The Show's snapshot surfaces the ruling for the verdict pane.
    verdict_vm = session.snapshot().get("verdict")
    assert verdict_vm and verdict_vm.get("winner")


def test_session_force_verdict_false_without_a_judge() -> None:
    session = _warm(_NO_JUDGE)
    assert session.force_verdict() is False
    assert not session.has_verdict()
    # No judge → no premature run.finished from the curtain call.
    assert not [e for e in session.events if e.kind == "run.finished"]


# ── the budget-then-verdict attribution gap (run_index last-wins) ──────────────────


def test_verdict_supersedes_a_winnerless_budget_close() -> None:
    # The common limit path: the governor trips (run finalized "budget", no winner)
    # BEFORE the judge rules.  The curtain call must still attribute the win — the
    # leaderboard reads run.finished last-wins, so a corrective close is appended.
    from src.core.run_index import index_runs

    session = _warm(_JUDGED)
    # Pre-finalize the run "budget" with no winner, exactly as a tripped step_one does.
    session.conductor.finalize("budget")
    pre = [e for e in session.events if e.kind == "run.finished"]
    assert len(pre) == 1 and pre[0].payload.get("reason") == "budget"

    assert session.force_verdict() is True
    # A corrective run.finished now carries the verdict + winner; the leaderboard folds
    # run.finished last-wins, so the indexed summary reads the ruling, not the truncation.
    summary = next(s for s in index_runs(session.events) if s.run_id == session.conductor.run_id)
    assert summary.reason == "verdict"
    assert summary.winner


def test_finalize_idempotent_for_nonbudget_close() -> None:
    # The scoped supersede must NOT fire for a non-budget prior close: a user stop stands.
    session = _warm(_JUDGED)
    first = session.conductor.finalize("user_stop")
    second = session.conductor.finalize("verdict", winner=session.scenario.agents[0].name)
    assert first is not None and second is not None and first.id == second.id  # same event
    finished = [e for e in session.events if e.kind == "run.finished"]
    assert len(finished) == 1 and finished[0].payload.get("reason") == "user_stop"
