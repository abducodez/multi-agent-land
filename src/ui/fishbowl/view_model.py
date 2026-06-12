"""``view_model_at`` — a JSON-serialisable snapshot of the world at a scrubbed step.

This is the single object the Show renders: cast cards, the narrator feed, meters, the
verdict.  It is a pure function of ``events[:k]`` (the same prefix-replay discipline as
``rebuild_stage``), so the transport can scrub anywhere and a future ``gr.Server`` can
serve the very same dict as JSON.  Token/round meters read real data from the run rather
than the prototype's fakes (G9).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from src.core.events import Event
from src.core.governor import Governor
from src.core.manifest import AgentManifest
from src.core.projections import rebuild_stage
from src.models.openai_compat import has_live_credentials
from src.models.provider import estimate_tokens
from src.ui.fishbowl.adapter import (
    VOICES,
    agent_archetype,
    agent_hue,
    agent_model,
    agent_tier,
    event_to_feed_item,
    mood_label,
    normalize_mood,
    scenario_voice,
    short_model_name,
)
from src.ui.fishbowl.cast_state import derive_cast_state

# Kinds whose actor, when the head event, lights the "speaking" ring on a card.
_SPEAKING_KINDS = frozenset({"agent.spoke", "agent.thought", "oracle.spoke", "judge.verdict"})


def _estimate_tokens_through(events: Sequence[Event]) -> int:
    """A real-text token estimate for the scrubber meter (grows as you advance)."""
    total = 0
    for e in events:
        text = e.payload.get("text") or e.payload.get("summary") or ""
        total += estimate_tokens(str(text))
    return total


def view_model_at(
    events: Iterable[Event],
    k: int,
    cast: Sequence[AgentManifest],
    *,
    scenario_name: str = "",
    goal: str = "",
    governor: Governor | None = None,
    voice: str | None = None,
    token_ceiling: int | None = None,
    max_rounds: int | None = None,
) -> dict:
    """Build the Show's snapshot at step *k* (clamped to ``[0, len(events)]``)."""
    events = tuple(events)
    n = len(events)
    k = max(0, min(int(k), n))
    prefix = events[:k]

    stage = rebuild_stage(prefix)
    names = [m.name for m in cast]
    states = derive_cast_state(prefix, names)

    # The model that *actually* produced each actor's most recent line (envelope,
    # ADR-0028) — overrides the card's intended binding once a line has been spoken.
    actual_model: dict[str, str] = {}
    for e in prefix:
        if e.model_id and e.actor in names:
            actual_model[e.actor] = e.model_id

    speaking_id: str | None = None
    if k > 0:
        head = events[k - 1]
        if (head.kind in _SPEAKING_KINDS or "text" in head.payload) and head.actor in names:
            speaking_id = head.actor

    cast_vm = []
    for m in cast:
        st = states[m.name]
        cast_vm.append(
            {
                "id": m.name,
                "name": m.name,
                "archetype": agent_archetype(m),
                "hue": agent_hue(m),
                "role": m.role,
                "model_profile": m.model_profile,
                # Prefer the model that actually ran; fall back to the intended binding.
                "model": short_model_name(actual_model[m.name]) if m.name in actual_model else agent_model(m),
                "model_id": actual_model.get(m.name),
                "model_endpoint": getattr(m, "model_endpoint", None),
                "tier": agent_tier(m),
                "said": st.said,
                "thought": st.thought,
                "mood": normalize_mood(st.mood),
                "mood_label": mood_label(st.mood),
                "spoke": st.spoke,
                "speaking": m.name == speaking_id,
            }
        )

    feed = []
    for e in prefix:
        item = event_to_feed_item(e, names)
        if item is not None:
            item["turn"] = e.turn
            feed.append(item)

    verdict = None
    for e in prefix:
        if e.kind == "judge.verdict":
            verdict = {
                "text": e.payload.get("text", ""),
                "reveal": e.payload.get("reveal", []),
                "agent": e.actor,
            }

    # "Round" = the live iteration count, i.e. the sim-turn at the play-head, so the
    # meter visibly climbs (1 → max_rounds) as the cast takes turns rather than freezing
    # until a visitor pokes the wood. max_rounds is the governor's max_turns ceiling.
    rounds = max((e.turn for e in prefix), default=1) or 1
    chosen_voice = voice or scenario_voice(scenario_name)
    voice_name, voice_desc = VOICES.get(chosen_voice, ("NARRATOR", ""))

    # Live vs. offline-stub binding (drives the meters' LIVE/OFFLINE pill) and the
    # governor's real spend, read from the same stats dict that carries tokens_real.
    stats = dict(governor.stats) if governor is not None else None
    spend_usd = float(stats["spend_usd"]) if stats is not None and "spend_usd" in stats else 0.0

    return {
        "step": k,
        "total": n,
        "scene": stage.current_scene,
        "seed": stage.seed,
        "goal": goal or stage.goal,
        "cast": cast_vm,
        "feed": feed,
        "voice": chosen_voice,
        "voice_meta": {"name": voice_name, "desc": voice_desc},
        "speaking_id": speaking_id,
        "verdict": verdict,
        "rounds": rounds,
        "max_rounds": max_rounds,
        "tokens": _estimate_tokens_through(prefix),
        "tokens_real": stats,
        "token_ceiling": token_ceiling,
        "offline": not has_live_credentials(),
        "spend_usd": spend_usd,
    }
