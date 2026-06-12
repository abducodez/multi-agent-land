"""Competition handlers — turning a judge's ruling into a machine-readable winner.

The arena contract (ADR-0029) says a winner is *data*, not prose: a
``judge.verdict`` event should carry ``payload["winner"]`` naming a cast member,
so the leaderboard can attribute the win to that agent's model.  Two layers make
that true:

  * **The LLM provides the drama.**  A judge manifest lists ``winner`` in
    ``output_extra_fields`` (``src/core/structured.py``), so on the live path the
    model is *required* to emit a ``winner`` string alongside its verdict text.
  * **Code provides the scoreboard.**  :class:`JudgedCompetition` validates that
    string against the agents actually on stage and, on any miss (a hallucinated
    name, or the offline stub which can't know the cast), falls back to a
    deterministic real candidate — so the verdict *always* names a genuine player.
    This keeps the no-API-key offline demo watchable: the winner is real even when
    the model never produced a usable one.

A judge with a known ground truth (The Steeped's :class:`SpyHost`, Twenty Sprouts'
``SproutJudge``) computes the winner in code instead — those subclass this and
override :meth:`decide_winner`.  This is the best-practice split the roadmap calls
for: AI is load-bearing for judgment, code is load-bearing for bookkeeping.
"""

from __future__ import annotations

from src.agents.base import ManifestAgent
from src.core.events import Event
from src.core.projections import StageProjection
from src.core.registry import register_handler

# Kinds a *competitor* emits — used to find who is eligible to win.  A judge emits
# ``judge.verdict`` and is excluded; the scene narrator emits ``world.observed`` (and at
# genesis its actor is the *scenario name*, not a player), so that is excluded too.
# Candidates are thus only the minds that actually spoke, derived from the run's events.
_COMPETITOR_KINDS = frozenset({"agent.spoke", "agent.thought", "oracle.spoke"})


@register_handler("judged-competition")
class JudgedCompetition(ManifestAgent):
    """A judge whose ``winner`` is validated against the live cast (and repaired offline).

    The generic turn produces the verdict text plus a ``winner`` field (because the
    manifest lists it in ``output_extra_fields``).  This handler then guarantees
    ``payload["winner"]`` is a real on-stage competitor: if the model's value isn't
    one (offline stub, or a live hallucination), it derives one — first by reading a
    name out of the verdict prose, then by falling back to the most active competitor.
    Deterministic, so offline runs are reproducible.
    """

    def act(
        self,
        run_id: str,
        turn: int,
        projection: StageProjection,
        recent_events: tuple[Event, ...],
    ) -> Event:
        event = super().act(run_id, turn, projection, recent_events)
        candidates = self._candidates(recent_events)
        if not candidates:
            return event  # nothing to attribute — leave the verdict as prose only
        winner = self.decide_winner(event, candidates, recent_events)
        if winner:
            event.payload["winner"] = winner
        return event

    # ── decision (override for ground-truth judges) ──────────────────────────────

    def decide_winner(
        self,
        event: Event,
        candidates: list[str],
        recent_events: tuple[Event, ...],
    ) -> str | None:
        """Return the winning cast name.

        Honours the model's ``winner`` when it names a real competitor; otherwise
        repairs it deterministically (prose mention → most-active fallback)."""
        named = (event.payload.get("winner") or "").strip()
        if named in candidates:
            return named
        return self._winner_from_prose(event, candidates) or self._most_active(candidates, recent_events)

    # ── helpers ──────────────────────────────────────────────────────────────────

    def _candidates(self, recent_events: tuple[Event, ...]) -> list[str]:
        """On-stage competitors: actors who actually spoke, minus this judge.

        Insertion-ordered (first appearance) so the fallback is stable and readable.
        """
        seen: dict[str, None] = {}
        for e in recent_events:
            if e.kind in _COMPETITOR_KINDS and e.actor and e.actor != self.name:
                seen.setdefault(e.actor, None)
        return list(seen)

    @staticmethod
    def _winner_from_prose(event: Event, candidates: list[str]) -> str | None:
        """Find the candidate the verdict text names, by full-slug substring match.

        Matches on the exact cast slug (``debater-a``) — and the hyphen→space variant
        (``debater a``) a live model might write — so it distinguishes symmetric seats
        that share a stem (``debater-a`` vs ``debater-b``) instead of matching the stem.
        When several are named, the earliest-mentioned wins (the one the judge leads
        with).  Returns ``None`` when the prose names no competitor."""
        text = (event.payload.get("text") or "").lower()
        best: str | None = None
        best_pos = len(text) + 1
        for name in candidates:
            for needle in (name.lower(), name.lower().replace("-", " ")):
                pos = text.find(needle)
                if 0 <= pos < best_pos:
                    best, best_pos = name, pos
                    break
        return best

    @staticmethod
    def _most_active(candidates: list[str], recent_events: tuple[Event, ...]) -> str:
        """Deterministic fallback: the competitor who spoke most (ties → cast order)."""
        counts = {name: 0 for name in candidates}
        for e in recent_events:
            if e.actor in counts and e.kind in _COMPETITOR_KINDS:
                counts[e.actor] += 1
        return max(candidates, key=lambda name: (counts[name], -candidates.index(name)))
