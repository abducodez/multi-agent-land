"""Agent memory — episodic recall, salience scoring, and reflection.

Memory architecture (three layers):

  1. EpisodicMemory — filtered view over the ledger; agents see only events
     they witnessed.  This is the simplest and always-on layer.

  2. SalienceMemory — ranks visible events by a composite score:
       salience(e) = w_rel·relevance(e,query) + w_rec·recency(e,turn) + w_imp·importance(e.kind)
     and returns the top-K rather than the most-recent K.  This layer is
     optional (manifest.memory.use_salience=True) and adds ~0 latency.

  3. ReflectionMemory — wraps either layer and emits an agent.reflected
     event every threshold events, compacting episodic memories into
     a high-level belief.  Reflection events are themselves visible to
     the agent, so beliefs accumulate over time without blowing the window.

None of these layers maintain separate persistent state — they are pure
functions over the shared append-only ledger.  Memory is always consistent
with the ledger because it *is* the ledger.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from src.core.events import Event

# ── importance weights by event kind ─────────────────────────────────────────

_KIND_IMPORTANCE: dict[str, float] = {
    "run.started": 0.3,
    "world.observed": 0.7,
    "agent.spoke": 0.5,
    "agent.thought": 0.4,
    "agent.reflected": 0.85,  # reflections are high-value compact memories
    "judge.verdict": 0.9,
    "user.injected": 0.95,    # visitor events are always salient
    "hypothesis.proposed": 0.75,
    "clue.found": 0.8,
    "verdict.final": 1.0,
}

_GLOBALLY_VISIBLE: frozenset[str] = frozenset(
    {"world.observed", "judge.verdict", "user.injected", "run.started", "agent.reflected"}
)

# ── layer 1: episodic memory ─────────────────────────────────────────────────

@dataclass
class EpisodicMemory:
    """Per-agent filtered view over the ledger — the always-on memory layer.

    An agent sees its own events plus globally-visible event kinds.
    The window is capped at max_recent to stay within small-model context budgets.
    """

    agent_name: str
    max_recent: int = 8

    def visible(self, events: tuple[Event, ...]) -> list[Event]:
        result = [
            e for e in events
            if e.actor == self.agent_name or e.kind in _GLOBALLY_VISIBLE
        ]
        return result[-self.max_recent:]

    def format_for_prompt(self, events: tuple[Event, ...]) -> str:
        recalled = self.visible(events)
        if not recalled:
            return "(no prior memory)"
        lines = []
        for e in recalled:
            text = e.payload.get("text") or e.payload.get("summary") or str(e.payload)
            lines.append(f"[turn {e.turn:03d}][{e.kind}] {text}")
        return "\n".join(lines)


# ── layer 2: salience-scored memory ──────────────────────────────────────────

@dataclass
class SalienceMemory:
    """Ranks visible events by salience instead of pure recency.

    salience(e) = w_rel·relevance + w_rec·recency + w_imp·importance

    relevance:  keyword overlap between event text and the current scene.
                Replace with cosine(embedding(e), embedding(scene)) in Phase 3.
    recency:    exponential decay — exp(−λ·Δturn).  λ=0.1 gives half-life ≈7 turns.
    importance: event-kind weight from _KIND_IMPORTANCE table.
    """

    agent_name: str
    top_k: int = 8
    w_relevance: float = 0.3
    w_recency: float = 0.4
    w_importance: float = 0.3
    decay_lambda: float = 0.1

    def score(self, event: Event, current_turn: int, query: str) -> float:
        recency = math.exp(-self.decay_lambda * max(0, current_turn - event.turn))
        importance = _KIND_IMPORTANCE.get(event.kind, 0.5)
        event_text = str(event.payload.get("text", "")).lower()
        query_words = set(query.lower().split())
        event_words = set(event_text.split())
        if not query_words or not event_words:
            relevance = 0.0
        else:
            relevance = len(query_words & event_words) / len(query_words | event_words)
        return (
            self.w_relevance * relevance
            + self.w_recency * recency
            + self.w_importance * importance
        )

    def visible(self, events: tuple[Event, ...], current_turn: int, query: str) -> list[Event]:
        candidates = [
            e for e in events
            if e.actor == self.agent_name or e.kind in _GLOBALLY_VISIBLE
        ]
        scored = sorted(
            candidates,
            key=lambda e: self.score(e, current_turn, query),
            reverse=True,
        )
        # Return in chronological order so prompts read naturally
        top = scored[: self.top_k]
        return sorted(top, key=lambda e: e.turn)

    def format_for_prompt(
        self, events: tuple[Event, ...], current_turn: int, query: str
    ) -> str:
        recalled = self.visible(events, current_turn, query)
        if not recalled:
            return "(no salient memories)"
        lines = []
        for e in recalled:
            text = e.payload.get("text") or e.payload.get("summary") or str(e.payload)
            s = self.score(e, current_turn, query)
            lines.append(f"[turn {e.turn:03d}][{e.kind}][sal={s:.2f}] {text}")
        return "\n".join(lines)


# ── layer 3: reflection trigger ───────────────────────────────────────────────

@dataclass
class ReflectionTracker:
    """Tracks whether this agent is due to emit a reflection event.

    Reflection events compact recent episodic memories into a high-level
    belief ("the baker resents me") that is cheaper to carry than raw history
    and richer.  The belief becomes an agent.reflected event in the ledger —
    which EpisodicMemory picks up in future turns because it is globally visible.
    """

    agent_name: str
    threshold: int  # emit reflection every N visible events
    _seen_count: int = field(default=0, init=False, repr=False)

    def observe(self, events: tuple[Event, ...]) -> bool:
        """Return True when a reflection should be emitted this turn."""
        visible_count = sum(
            1 for e in events
            if e.actor == self.agent_name or e.kind in _GLOBALLY_VISIBLE
        )
        due = (
            visible_count > 0
            and visible_count != self._seen_count
            and visible_count % self.threshold == 0
        )
        self._seen_count = visible_count
        return due
