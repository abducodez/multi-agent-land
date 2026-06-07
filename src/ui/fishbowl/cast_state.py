"""Per-agent stage state — a pure projection of the ledger (G1, ADR-0021).

The engine's :class:`StageProjection` keeps a flat ``agent_notes`` list; the Fishbowl
MindCard needs, per mind, its latest public ``said``, private ``thought``, and current
``mood``.  ``derive_cast_state`` is the missing projection: like ``rebuild_stage`` it is
a pure function of an events slice, so the UI can show the world at any scrubbed step
``k`` by passing ``events[:k]`` — and it never mutates the log.

The say-vs-think pairing rides on optional payload fields (ADR-0009): an agent that
emits ``agent.spoke`` carries ``thought``/``mood`` alongside ``text``; an agent that
emits ``agent.thought`` puts its inner line in ``text`` directly.  Both are produced by
the model live and by the deterministic stub offline, so the mind-reader works with no
API key.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.core.events import Event

# Kinds whose ``text`` is a public utterance (the front-of-card "said" line).
_SAID_KINDS = frozenset({"agent.spoke", "world.observed", "oracle.spoke"})
# Kinds whose ``text`` is itself the private thought.
_THINK_KINDS = frozenset({"agent.thought"})
# A judge's ruling — shown as that mind's "said" (and separately as a verdict).
_VERDICT_KINDS = frozenset({"judge.verdict"})
# Never alters a mind's said/thought (genesis + private memory compaction).
_IGNORED_KINDS = frozenset({"run.started", "agent.reflected"})


@dataclass
class CastMemberState:
    """The current say/think/mood of one mind, derived from the ledger."""

    said: str | None = None
    thought: str | None = None
    mood: str = "calm"
    spoke: bool = False
    last_turn: int | None = None


def derive_cast_state(
    events: Iterable[Event],
    cast_names: Iterable[str],
) -> dict[str, CastMemberState]:
    """Replay *events* into ``{agent_name: CastMemberState}`` — pure and deterministic.

    Events from actors not in *cast_names* (e.g. ``conductor``, ``visitor``) are
    ignored here; they surface in the narrator feed / poke strip instead.
    """
    state = {name: CastMemberState() for name in cast_names}
    for e in events:
        st = state.get(e.actor)
        if st is None or e.kind in _IGNORED_KINDS:
            continue
        text = e.payload.get("text")
        if e.kind in _SAID_KINDS or e.kind in _VERDICT_KINDS:
            if text is not None:
                st.said = str(text)
            st.spoke = True
        elif e.kind in _THINK_KINDS:
            if text is not None:
                st.thought = str(text)
        elif text is not None:
            # A custom namespaced kind that carries text → treat as an utterance,
            # so a drop-in agent renders on stage with zero presenter edits.
            st.said = str(text)
            st.spoke = True
        # Paired private thought / mood ride as optional payload fields (ADR-0021).
        if e.payload.get("thought"):
            st.thought = str(e.payload["thought"])
        if e.payload.get("mood"):
            st.mood = str(e.payload["mood"])
        st.last_turn = e.turn
    return state
