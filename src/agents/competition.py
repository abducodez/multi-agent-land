"""Competition handlers — turning a judge's ruling into a machine-readable winner.

The arena contract (ADR-0029) says a winner is *data*, not prose: a
``judge.verdict`` carries ``payload["winner"]`` naming a cast member (or team), so
the leaderboard can attribute the win to a model.  Responsibilities split cleanly:

  * **The model + the engine handle the live path.**  A judge manifest lists
    ``winner`` in ``output_extra_fields`` (a well-known typed field,
    ``src/core/structured.py``), and :class:`~src.agents.base.ManifestAgent`
    validates that name against the injected ``cast_names`` — one corrective re-ask,
    then ``no_contest`` if the model still won't name a real player.
  * **This handler keeps the OFFLINE demo watchable.**  The deterministic stub never
    emits a ``winner`` (the field is optional, ADR-0029), so without help an offline
    judged run would crown no one.  :class:`JudgedCompetition` fills an *empty* winner
    by reading the name out of the verdict prose, then falling back to the most active
    competitor — deterministic, so offline runs are reproducible.  It defers entirely
    when the engine already forfeited the round (``no_contest``).

A judge with a known ground truth (The Steeped's :class:`~src.agents.handlers.SpyHost`,
Twenty Sprouts' :class:`~src.agents.twenty_sprouts.SproutJudge`) computes the winner in
code instead — ``SproutJudge`` subclasses this and overrides :meth:`decide_winner`.
The best-practice split the roadmap calls for: AI is load-bearing for judgment, code is
load-bearing for bookkeeping.
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
    """A judge that fills an empty offline ``winner`` so the stub demo still crowns one.

    The generic turn (and the engine's live validation) handle a model-named winner.
    This handler runs *after* that: when the verdict carries no winner — the offline
    stub, which never emits the field — it derives one from the verdict prose, then
    from the most active competitor.  It honours a model-named winner that is already a
    real competitor, and defers when the engine forfeited the round (``no_contest``).
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
            # We crowned someone — clear any forfeit the engine stamped (a ground-truth
            # judge overrides no-contest; a repaired offline winner supersedes the empty).
            event.payload.pop("no_contest", None)
        return event

    # ── decision (override for ground-truth judges) ──────────────────────────────

    def decide_winner(
        self,
        event: Event,
        candidates: list[str],
        recent_events: tuple[Event, ...],
    ) -> str | None:
        """Return the winning cast name (or team label).

        Honours the model's ``winner`` when it already names a real competitor or team;
        otherwise repairs it deterministically (prose mention → most-active fallback).
        Defers when the engine forfeited the round (``no_contest``) — a live model that
        refused to name a real player keeps its forfeit; only the offline empty-winner
        path (no forfeit) is repaired.  Ground-truth judges override this and ignore
        ``no_contest`` so their code-decided winner always lands."""
        if event.payload.get("no_contest"):
            return None
        named = (event.payload.get("winner") or "").strip()
        if named and (named in candidates or named in self._team_labels()):
            return named
        return self._winner_from_prose(event, candidates) or self._most_active(candidates, recent_events)

    # ── helpers ──────────────────────────────────────────────────────────────────

    def _team_labels(self) -> set[str]:
        """Valid team labels for this scenario (empty for judged / symmetric-seat duels)."""
        return set((getattr(self.competition, "teams", None) or {}).keys())

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
