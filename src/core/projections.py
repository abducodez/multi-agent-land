from __future__ import annotations

from dataclasses import dataclass, field

from src import observability as obs
from src.core.events import Event


@dataclass
class StageProjection:
    seed: str = ""
    goal: str = ""
    current_scene: str = "The curtain has not risen."
    agent_notes: list[str] = field(default_factory=list)
    judge_notes: list[str] = field(default_factory=list)
    user_artifacts: list[str] = field(default_factory=list)

    def apply(self, event: Event) -> None:
        obs.log("projection.apply", level="debug", kind=event.kind, actor=event.actor, turn=event.turn)
        if event.kind == "run.started":
            self.seed = str(event.payload["seed"])
            self.goal = str(event.payload.get("goal", "")) or self.goal
            self.current_scene = f"The wood wakes around: {self.seed}"
        elif event.kind == "world.observed":
            self.current_scene = str(event.payload["text"])
        elif event.kind == "agent.reflected":
            self.agent_notes.append(f"💭 {event.actor} believes: {event.payload.get('text', '')}")
            self.agent_notes = self.agent_notes[-8:]
        elif event.kind in {"agent.thought", "agent.spoke"}:
            self.agent_notes.append(f"{event.actor}: {event.payload['text']}")
            self.agent_notes = self.agent_notes[-8:]
        elif event.kind == "judge.verdict":
            self.judge_notes.append(str(event.payload["text"]))
            self.judge_notes = self.judge_notes[-5:]
        elif event.kind == "user.injected":
            self.user_artifacts.append(str(event.payload["text"]))
            self.user_artifacts = self.user_artifacts[-5:]
        elif "text" in event.payload:
            # Generic fallback: any drop-in agent that mints its own namespaced
            # kind (e.g. "clue.found", "oracle.spoke") still renders on stage with
            # zero engine edits, as long as it carries a `text` payload.
            self.agent_notes.append(f"{event.actor} [{event.kind}]: {event.payload['text']}")
            self.agent_notes = self.agent_notes[-8:]


def rebuild_stage(events: tuple[Event, ...], run_id: str | None = None) -> StageProjection:
    projection = StageProjection()
    if run_id is not None:
        events = tuple(e for e in events if e.run_id == run_id)
    for event in events:
        projection.apply(event)
    return projection
