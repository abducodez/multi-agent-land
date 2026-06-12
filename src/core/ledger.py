from __future__ import annotations

from collections.abc import Iterable

from src import observability as obs
from src.core.events import Event


class Ledger:
    """Append-only in-memory ledger for the first vertical slice."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._seen_ids: set[str] = set()

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def append(self, event: Event) -> Event:
        if event.id in self._seen_ids:
            return event
        self._events.append(event)
        self._seen_ids.add(event.id)
        obs.log("ledger.append", level="debug", id=event.id, kind=event.kind, actor=event.actor, turn=event.turn)
        obs.incr("ledger.events", 1, kind=event.kind)
        return event

    def extend(self, events: Iterable[Event]) -> None:
        for event in events:
            self.append(event)

    def reset(self) -> None:
        obs.log("ledger.reset", level="debug", events=len(self._events))
        self._events.clear()
        self._seen_ids.clear()

    def events_for_run(self, run_id: str) -> tuple[Event, ...]:
        """Return the events of *run_id* in append/offset order."""
        return tuple(e for e in self._events if e.run_id == run_id)

    def runs(self) -> tuple[str, ...]:
        """Return the distinct run_ids in first-seen order."""
        seen: dict[str, None] = {}
        for e in self._events:
            seen.setdefault(e.run_id, None)
        return tuple(seen)
