from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src import observability as obs

# ── event kinds ───────────────────────────────────────────────────────────────
#
# `kind` is an OPEN, format-validated string — NOT a closed enum.  This is the
# modularity contract for the event schema (ADR-0009): a new scenario can mint
# its own namespaced kinds ("clue.found", "image.generated", "episode.published")
# without editing this file.  The schema validates the *shape* of a kind; the
# *authority* to emit a given kind is enforced per-agent by `manifest.may_emit`.
#
# A kind is a lowercase, dot-namespaced identifier with at least two segments:
#     <namespace>.<name>   e.g. "agent.spoke", "clue.found", "episode.published"

# Documented core payload shapes (the schema validates kind *format*, not payload —
# these are conventions for the engine's own events):
#
#   run.finished payload:
#       {
#           "reason": "verdict" | "budget" | "tick_cap" | "user_stop",
#           "winner": str | None,           # actor name of the winner, if any
#           "winning_model": str | None,    # model bound to the winner, if known
#           "turns": int,                   # turns elapsed in the run
#           "tokens": int,                  # total tokens spent in the run
#       }
#
#   run.started payload is being ENRICHED in a later step (scenario name +
#   cast->model map).  That change is purely ADDITIVE — new keys alongside the
#   existing ones — so `schema_version` stays 1 (no migration required).

CORE_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "run.started",
        "run.finished",
        "world.observed",
        "agent.thought",
        "agent.spoke",
        "agent.reflected",
        "judge.verdict",
        "user.injected",
    }
)

_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$")

# Backward-compatible alias.  Annotations that referenced `EventKind` still work;
# it is now an open (validated) string rather than a Literal union.
EventKind = str


def is_valid_kind(kind: str) -> bool:
    """True if *kind* is a well-formed, dot-namespaced event kind."""
    return bool(_KIND_PATTERN.match(kind))


# ── session ids ───────────────────────────────────────────────────────────────
#
# A session id attributes a run (and, via the envelope below, every event in it)
# to the browser/user that started it.  The value originates client-side
# (localStorage), so it is UNTRUSTED input: normalize at the engine boundary
# before it ever reaches the ledger or the memory index.

_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def normalize_session_id(value: str | None) -> str | None:
    """Return a safe session id, or ``None`` when *value* is absent or malformed.

    Accepts the ids we mint (UUIDs, ``sess-…``) and rejects anything else —
    over-long strings, whitespace, control characters, separators that could
    confuse downstream filters.  Rejection degrades to an unattributed run
    rather than an error, so a tampered localStorage never breaks Summon.
    """
    candidate = (value or "").strip()
    if not candidate:
        return None
    if _SESSION_ID_PATTERN.match(candidate):
        return candidate
    obs.log("session.id_rejected", level="warning", length=len(candidate))
    return None


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    turn: int
    kind: EventKind
    actor: str
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    schema_version: int = 1
    # Who (which browser/user session) drove the run this event belongs to.
    # Stamped by the Conductor on append (single chokepoint), nullable for
    # headless/legacy events.  An OPTIONAL envelope field — additive, so
    # schema_version stays 1 and old rows load with session_id=None.
    session_id: str | None = None
    # Which model produced this event, set by the agent at generation time:
    #   model_profile — the route key the agent asked for (tiny/fast/balanced/
    #                    strong, or an explicit catalogue endpoint key, ADR-0022)
    #   model_id       — the concrete model that actually ran (e.g.
    #                    "openai/openbmb/MiniCPM4.1-8B", or "stub:fast" offline)
    # Both None for events with no model behind them (genesis, user.injected,
    # run.started/finished).  Additive envelope fields — schema_version stays 1.
    model_profile: str | None = None
    model_id: str | None = None

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        if not is_valid_kind(value):
            obs.log("event.invalid", level="warning", kind=value)
            raise ValueError(
                f"invalid event kind {value!r}: must be a lowercase, dot-namespaced "
                "identifier such as 'agent.spoke' or 'clue.found'"
            )
        return value


def event_summary(event: Event) -> str:
    text = event.payload.get("text") or event.payload.get("summary") or event.payload
    return f"{event.turn:03d} {event.kind:<15} {event.actor:<14} {text}"
