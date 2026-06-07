from __future__ import annotations

from dataclasses import dataclass, field

from src.core.events import Event


@dataclass
class EpisodicMemory:
    """Per-agent view over the shared ledger.

    Keeps the N most recent events that are visible to this agent:
    its own actions, world observations, judge verdicts, and anything
    a visitor injected.  Everything else is noise for the prompt.
    """

    agent_name: str
    max_recent: int = 8
    _visible_kinds: frozenset[str] = field(
        default_factory=lambda: frozenset(
            {"world.observed", "judge.verdict", "user.injected", "run.started"}
        ),
        repr=False,
    )

    def visible(self, events: tuple[Event, ...]) -> list[Event]:
        result = []
        for e in events:
            if e.actor == self.agent_name or e.kind in self._visible_kinds:
                result.append(e)
        return result[-self.max_recent :]

    def format_for_prompt(self, events: tuple[Event, ...]) -> str:
        recalled = self.visible(events)
        if not recalled:
            return "(no prior memory)"
        lines = []
        for e in recalled:
            text = e.payload.get("text") or e.payload.get("summary") or str(e.payload)
            lines.append(f"[turn {e.turn:03d}][{e.kind}] {text}")
        return "\n".join(lines)
