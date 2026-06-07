from __future__ import annotations

from collections import Counter

from src.core.events import Event, event_summary
from src.core.governor import Governor
from src.core.projections import StageProjection
from src.scenarios.base import Scenario


def render_stage(projection: StageProjection) -> str:
    goal = f"> **Goal:** {projection.goal}\n\n" if projection.goal else ""
    artifacts = "\n".join(f"- {item}" for item in projection.user_artifacts) or "- No visitor artifacts yet."
    notes = "\n".join(f"- {item}" for item in projection.agent_notes) or "- Agents are waiting."
    verdicts = "\n".join(f"- {item}" for item in projection.judge_notes) or "- No verdict yet."
    return f"""## Current Clearing

{goal}{projection.current_scene}

### Visitor Disturbances
{artifacts}

### Agent Activity
{notes}

### Judge Notes
{verdicts}
"""


def render_event_log(events: tuple[Event, ...]) -> str:
    if not events:
        return "(ledger is empty)"
    return "\n".join(event_summary(event) for event in events)


def render_stats(events: tuple[Event, ...], governor: Governor | None = None) -> str:
    by_kind = Counter(event.kind for event in events)
    by_actor = Counter(event.actor for event in events)
    lines = ["Events by kind:"]
    lines.extend(f"  {key}: {value}" for key, value in sorted(by_kind.items()))
    lines.append("")
    lines.append("Events by actor:")
    lines.extend(f"  {key}: {value}" for key, value in sorted(by_actor.items()))

    # Structured-output health: how often the parser had to fall back to raw text.
    content = [e for e in events if e.actor not in ("conductor", "visitor")]
    fallbacks = sum(1 for e in content if e.payload.get("_raw_fallback"))
    if content:
        lines.append("")
        lines.append(f"Structured output: {len(content) - fallbacks}/{len(content)} clean JSON "
                     f"({fallbacks} raw fallback)")

    lines.append("")
    lines.append("Hackathon constraints:")
    lines.append("  runtime model cap: <=32B")
    lines.append("  tiny mode target: <=4B")
    lines.append("  UI target: custom Gradio")
    if governor is not None:
        lines.append("")
        lines.append("Governor:")
        for k, v in governor.stats.items():
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def render_config(scenario: Scenario, profile_models: dict[str, str] | None = None) -> str:
    """The 'config as data' panel — the live, declarative makeup of the run.

    Everything shown here comes from YAML (config/), not code: the cast that
    participates, each agent's model tier, what it may emit, and its tool grants.
    """

    rows = ["| agent | role | model | emits | tools |", "|---|---|---|---|---|"]
    for agent in scenario.agents:
        manifest = getattr(agent, "manifest", None)
        if manifest is None:
            rows.append(f"| {agent.name} | (legacy) | — | — | — |")
            continue
        emits = ", ".join(manifest.may_emit) or "—"
        tools = ", ".join(manifest.tools) or "—"
        rows.append(
            f"| `{manifest.name}` | {manifest.role} | `{manifest.model_profile}` | {emits} | {tools} |"
        )
    table = "\n".join(rows)

    profile_block = ""
    if profile_models:
        profile_lines = "\n".join(f"- `{p}` → `{m}`" for p, m in profile_models.items())
        profile_block = f"\n\n**Model profiles**\n{profile_lines}"

    goal = f"\n\n**Goal**\n{scenario.goal}" if scenario.goal else ""
    return f"### Cast (from `config/`)\n\n{table}{profile_block}{goal}"
