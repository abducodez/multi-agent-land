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

CORE_EVENT_KINDS: frozenset[str] = frozenset(
    {
        "run.started",
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
