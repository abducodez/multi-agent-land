from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


EventKind = Literal[
    "run.started",
    "world.observed",
    "agent.thought",
    "agent.spoke",
    "judge.verdict",
    "user.injected",
]


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


def event_summary(event: Event) -> str:
    text = event.payload.get("text") or event.payload.get("summary") or event.payload
    return f"{event.turn:03d} {event.kind:<15} {event.actor:<14} {text}"

