from __future__ import annotations

from collections import Counter

from src.core.events import Event, event_summary
from src.core.projections import StageProjection


def render_stage(projection: StageProjection) -> str:
    artifacts = "\n".join(f"- {item}" for item in projection.user_artifacts) or "- No visitor artifacts yet."
    notes = "\n".join(f"- {item}" for item in projection.agent_notes) or "- Agents are waiting."
    verdicts = "\n".join(f"- {item}" for item in projection.judge_notes) or "- No verdict yet."
    return f"""
## Current Clearing

{projection.current_scene}

### Visitor Disturbances
{artifacts}

### Agent Activity
{notes}

### Judge Notes
{verdicts}
"""


def render_event_log(events: tuple[Event, ...]) -> str:
    return "\n".join(event_summary(event) for event in events)


def render_stats(events: tuple[Event, ...]) -> str:
    by_kind = Counter(event.kind for event in events)
    by_actor = Counter(event.actor for event in events)
    lines = ["Events by kind:"]
    lines.extend(f"  {key}: {value}" for key, value in sorted(by_kind.items()))
    lines.append("")
    lines.append("Events by actor:")
    lines.extend(f"  {key}: {value}" for key, value in sorted(by_actor.items()))
    lines.append("")
    lines.append("Hackathon constraints:")
    lines.append("  runtime model cap: <=32B")
    lines.append("  tiny mode target: <=4B")
    lines.append("  UI target: custom Gradio")
    return "\n".join(lines)

