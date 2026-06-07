from __future__ import annotations

from dataclasses import dataclass, field

from src.core.events import Event


@dataclass
class StageProjection:
    seed: str = ""
    current_scene: str = "The curtain has not risen."
    agent_notes: list[str] = field(default_factory=list)
    judge_notes: list[str] = field(default_factory=list)
    user_artifacts: list[str] = field(default_factory=list)

    def apply(self, event: Event) -> None:
        if event.kind == "run.started":
            self.seed = str(event.payload["seed"])
            self.current_scene = f"The wood wakes around: {self.seed}"
        elif event.kind == "world.observed":
            self.current_scene = str(event.payload["text"])
        elif event.kind in {"agent.thought", "agent.spoke"}:
            self.agent_notes.append(f"{event.actor}: {event.payload['text']}")
            self.agent_notes = self.agent_notes[-8:]
        elif event.kind == "judge.verdict":
            self.judge_notes.append(str(event.payload["text"]))
            self.judge_notes = self.judge_notes[-5:]
        elif event.kind == "user.injected":
            self.user_artifacts.append(str(event.payload["text"]))
            self.user_artifacts = self.user_artifacts[-5:]


def rebuild_stage(events: tuple[Event, ...]) -> StageProjection:
    projection = StageProjection()
    for event in events:
        projection.apply(event)
    return projection

