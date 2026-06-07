"""Read-only observer — the camera crew that watches the ledger and renders to the UI.

Design contract:
  - The observer NEVER appends events.  It is strictly read-only.
  - It maintains its own view-state as a projection, not as shared state.
  - It notifies registered callbacks when the view changes.
  - The world runs identically whether or not any observer is attached.
  - Multiple observers can subscribe to the same ledger simultaneously.

This decoupling gives two guarantees:
  1. The cognitive loop is reproducible without a UI.
  2. You can run multiple renderers off the same log — a stage view, a
     cognition-graph, a plain chat-log — without coupling to each other.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from src.core.events import Event
from src.core.projections import StageProjection


# ── diff model ────────────────────────────────────────────────────────────────

@dataclass
class ViewDiff:
    """What changed between two consecutive observer ticks.

    The UI agent computes this diff and streams only the delta to the client,
    rather than re-rendering the entire state every turn.  This is the right
    shape for SSE / WebSocket streaming.
    """

    scene_changed: bool = False
    new_scene: str = ""
    new_agent_notes: list[str] = field(default_factory=list)
    new_judge_notes: list[str] = field(default_factory=list)
    new_user_artifacts: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.scene_changed
            or self.new_agent_notes
            or self.new_judge_notes
            or self.new_user_artifacts
        )


# ── observer ─────────────────────────────────────────────────────────────────

class Observer:
    """Read-only subscriber over the event ledger.

    Usage:
        observer = Observer()
        observer.on_change(lambda diff: send_to_client(diff))

        # In the conductor loop:
        for event in new_events:
            observer.consume(event)
    """

    def __init__(self) -> None:
        self._view = StageProjection()
        self._callbacks: list[Callable[[ViewDiff], None]] = []

    # ── subscription ──────────────────────────────────────────────────────────

    def on_change(self, callback: Callable[[ViewDiff], None]) -> None:
        """Register a callback invoked whenever the view changes."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[ViewDiff], None]) -> None:
        self._callbacks = [cb for cb in self._callbacks if cb is not callback]

    # ── consumption ───────────────────────────────────────────────────────────

    def consume(self, event: Event) -> ViewDiff:
        """Apply one event and return the diff.  Notifies callbacks if anything changed."""
        prev_scene = self._view.current_scene
        prev_notes = list(self._view.agent_notes)
        prev_verdicts = list(self._view.judge_notes)
        prev_artifacts = list(self._view.user_artifacts)

        self._view.apply(event)

        diff = ViewDiff(
            scene_changed=self._view.current_scene != prev_scene,
            new_scene=self._view.current_scene if self._view.current_scene != prev_scene else "",
            new_agent_notes=[n for n in self._view.agent_notes if n not in prev_notes],
            new_judge_notes=[n for n in self._view.judge_notes if n not in prev_verdicts],
            new_user_artifacts=[a for a in self._view.user_artifacts if a not in prev_artifacts],
        )

        if diff.has_changes:
            for cb in self._callbacks:
                cb(diff)

        return diff

    def consume_batch(self, events: tuple[Event, ...]) -> list[ViewDiff]:
        """Consume multiple events in order; returns a diff per event."""
        return [self.consume(e) for e in events]

    # ── read access ───────────────────────────────────────────────────────────

    @property
    def view(self) -> StageProjection:
        """Current materialized view.  Read-only; do not mutate directly."""
        return self._view

    def reset(self) -> None:
        self._view = StageProjection()
