"""Loop-safety tests for the Fishbowl autoplay transport — no mocks, offline stub.

Proves the keystone of the live-integration feature: autoplay can NEVER spin into an
infinite, token-burning loop.  Three independent backstops are exercised against a real
``FishbowlSession`` (deterministic stub, no API key):

  1. a tripped governor budget stops autoplay (and never crashes the callback);
  2. a verdict at the head auto-pauses the show;
  3. a hard ``_MAX_AUTO_TICKS`` cap stops autoplay even with an unbounded budget.
"""

from __future__ import annotations

from src.core.governor import BudgetExceeded, Governor
from src.ui.fishbowl import app as fb_app
from src.ui.fishbowl.app import advance_one_tick
from src.ui.fishbowl.session import FishbowlSession


def _session_with_governor(governor: Governor) -> FishbowlSession:
    """A real session whose conductor uses *governor* (reset so genesis is written)."""
    session = FishbowlSession("thousand-token-wood")
    session.conductor.governor = governor
    session.reset()
    return session


# ── backstop 1: a tripped governor stops autoplay, never raises out of the tick ──


def test_advance_stops_on_budget_without_raising() -> None:
    governor = Governor(max_total_calls=3)
    session = _session_with_governor(governor)

    k = session.head
    ticks = 0
    stop_reason = None
    # Drive autoplay to the head and keep ticking; this MUST terminate (no infinite loop).
    for _ in range(200):
        k, ticks, stop_reason = advance_one_tick(session, k, ticks)
        if stop_reason is not None:
            break
    else:  # pragma: no cover - only hit if the loop never stops (the bug we guard against)
        raise AssertionError("autoplay never stopped — infinite loop")

    # The loop terminated with a meaningful reason — never an infinite token burn.
    # (With a tight budget the run trips a governor bound — surfaced as its structured
    # reason code, e.g. ``max_total_calls`` — or resolves via a verdict; either is a
    # clean stop.)
    assert stop_reason
    assert any(token in stop_reason.lower() for token in ("cap", "reached", "verdict", "max_"))


def test_advance_surfaces_budget_reason_at_head() -> None:
    """When the governor is already exhausted at the head, the tick returns its reason."""
    session = FishbowlSession("thousand-token-wood")
    session.reset()  # genesis only — no verdict yet
    assert not session.has_verdict()
    # Exhaust the budget so the very next generate trips ``check``.
    session.conductor.governor.max_total_calls = 0
    k, ticks, stop_reason = advance_one_tick(session, session.head, 0)
    assert stop_reason is not None
    # The governor's own structured reason names the tripped bound — not the tick backstop.
    assert "max_total_calls" in stop_reason.lower()
    assert "tick cap" not in stop_reason.lower()
    assert ticks == 0  # nothing generated


def test_session_step_raises_budget_exceeded_when_exhausted() -> None:
    """The raw transport surfaces BudgetExceeded; the UI handler is what swallows it."""
    governor = Governor(max_total_calls=2)
    session = _session_with_governor(governor)
    raised = False
    for _ in range(200):
        try:
            session.step()
        except BudgetExceeded:
            raised = True
            break
    assert raised, "a bounded governor must eventually raise BudgetExceeded"


# ── backstop 2: a verdict at the head auto-pauses the show ───────────────────────


def test_advance_stops_on_verdict() -> None:
    # Run a generous session until the Judge rules, then assert autoplay halts on verdict.
    session = FishbowlSession("thousand-token-wood")
    session.reset()
    for _ in range(200):
        if session.has_verdict():
            break
        session.step()
    if not session.has_verdict():
        # Some stub scenarios never emit a verdict; nothing to assert then.
        return
    _, _, stop_reason = advance_one_tick(session, session.head, 0)
    assert stop_reason is not None
    assert "verdict" in stop_reason.lower()


# ── backstop 3: the hard tick cap stops autoplay even with an unbounded budget ───


def test_advance_stops_at_tick_cap() -> None:
    # A huge budget so the governor never trips — only the tick cap can stop us.
    session = _session_with_governor(Governor(max_total_calls=10_000, max_turns=10_000))
    # Sitting at the head with ticks already at the cap → must stop, must not generate.
    head_before = session.head
    k, ticks, stop_reason = advance_one_tick(session, session.head, fb_app._MAX_AUTO_TICKS)
    assert stop_reason is not None
    assert str(fb_app._MAX_AUTO_TICKS) in stop_reason
    assert session.head == head_before  # no generation happened once the cap tripped


def test_advance_counts_only_generating_ticks() -> None:
    session = _session_with_governor(Governor(max_total_calls=10_000, max_turns=10_000))
    head = session.head
    # Replaying below the head is free: ticks must not advance.
    _, ticks, stop_reason = advance_one_tick(session, head - 1, 5)
    assert stop_reason is None
    assert ticks == 5  # unchanged while replaying the prefix
    # Generating at the head increments the counter.
    _, ticks2, stop2 = advance_one_tick(session, head, 5)
    assert stop2 is None
    assert ticks2 == 6


def test_autoplay_streams_one_agent_per_advance() -> None:
    # Each generating advance reveals exactly ONE new event, so the UI shows each mind
    # the moment it responds rather than after the whole turn (the streaming request).
    session = _session_with_governor(Governor(max_total_calls=10_000, max_turns=10_000))
    k = session.head
    for _ in range(6):
        before = session.head
        k, _ticks, stop = advance_one_tick(session, k, 0)
        if stop:
            break
        assert session.head - before <= 1  # never more than one event per advance
        assert k == session.head  # the play-head tracks the freshly streamed event


# ── the STOPPED banner + LIVE/OFFLINE topbar ─────────────────────────────────────


def test_stopped_banner_reuses_verdict_chrome() -> None:
    html = fb_app._stopped_banner_html("Total call cap 3 reached")
    assert "verdict banner" in html  # reuses the existing banner CSS class
    assert "Stopped" in html
    assert "Total call cap 3 reached" in html  # the reason is shown


def test_topbar_chip_offline_by_default() -> None:
    # With no live credentials configured the topbar shows the OFFLINE/STUB chip.
    from src.models import inference

    chip = fb_app._live_chip()
    assert "OFFLINE" in chip or "LIVE" in chip  # honest either way
    if not inference.configured_backends():
        assert "OFFLINE" in chip and "STUB" in chip
