"""Fishbowl · Lab — one agent's editable card.

A factory that emits the per-agent controls inside the Cast section: the model
dropdown (as before), plus the editable surface the engine actually consumes at
runtime and that is safe to patch via ``model_copy`` — a per-agent tool grant, a
persona override, and the schedule knobs (``tick_every`` / ``max_consecutive``).
Read-only facts (role, profile, what the agent listens for / may emit) ride along
as info chips so the user can edit with context without touching the fragile event
contract.

The factory wires nothing into the run on its own — it returns a small handle bundle
so :func:`build_lab` can connect each control's ``.change`` into the matching
``gr.State`` dict (one dict per editable field, keyed by agent name).  This keeps the
composer's "knobs → one validated WorldConfig" contract (ADR-0011 / ADR-0025) intact.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

import gradio as gr

from src.core.manifest import AgentManifest

# Soft per-turn cap for the schedule knobs — generous enough for any cast we ship,
# small enough that a slider stays legible.  The engine enforces the real bounds.
_TICK_MAX = 32
_CONSECUTIVE_MAX = 8


@dataclass
class AgentPanel:
    """Handles for one agent's card — the controls :func:`build_lab` wires up.

    ``tools`` / ``persona`` / ``tick_every`` / ``max_consecutive`` are None when the
    card omits that control (e.g. an agent with no tool grant has no tool picker), so
    the wiring loop can skip absent fields without guessing.
    """

    name: str
    model: gr.Dropdown
    tools: gr.CheckboxGroup | None = None
    persona: gr.Textbox | None = None
    tick_every: gr.Number | None = None
    max_consecutive: gr.Number | None = None


def _chip(label: str, value: str) -> str:
    """One read-only info chip (escaped); empty value → a muted dash."""
    safe = html.escape(value) if value else "—"
    return (
        f"<span class='lab-chip'><span class='lab-chip-k'>{html.escape(label)}</span>"
        f"<span class='lab-chip-v'>{safe}</span></span>"
    )


def _info_chips(manifest: AgentManifest) -> str:
    """The read-only fact strip: role, profile, and the (frozen) event contract."""
    subs = ", ".join(manifest.subscribes_to) if manifest.subscribes_to else "everything"
    emits = ", ".join(manifest.may_emit) if manifest.may_emit else "—"
    chips = "".join(
        [
            _chip("role", manifest.role),
            _chip("profile", manifest.model_profile),
            _chip("listens", subs),
            _chip("may emit", emits),
        ]
    )
    return f"<div class='lab-chips'>{chips}</div>"


def render_agent_panel(
    manifest: AgentManifest,
    *,
    model_choices: list[tuple[str, str]],
    model_value: str | None,
    backend_label: str,
    tool_choices: list[tuple[str, str]] | None = None,
) -> AgentPanel:
    """Render one agent's editable card and return its control handles.

    Called inside a ``gr.render`` (or any Blocks context).  *model_choices* /
    *model_value* / *backend_label* drive the model dropdown exactly as the old inline
    row did.  *tool_choices* is the set of (label, tool_id) the agent may be granted
    *and* that the engine actually has — when empty/None the tool picker is omitted, so
    a tool checkbox only ever appears for a tool-capable agent.
    """
    archetype = manifest.archetype or f"the {manifest.role}"
    with gr.Group(elem_classes=["lab-agent-card"]):
        gr.Markdown(
            f"<div class='lab-agent-head'>"
            f"<span class='lab-agent-name'>{html.escape(manifest.name)}</span>"
            f"<span class='lab-agent-arch'>{html.escape(archetype)}</span></div>"
            f"{_info_chips(manifest)}",
            elem_classes=["lab-agent-meta"],
        )
        model = gr.Dropdown(
            choices=model_choices,
            value=model_value,
            label=f"model · {backend_label}",
            interactive=bool(model_choices),
        )

        tools_group: gr.CheckboxGroup | None = None
        if tool_choices:
            tools_group = gr.CheckboxGroup(
                choices=tool_choices,
                value=list(manifest.tools),
                label="tool grants · MCP capabilities",
                info="Capability-checked: the mind may only call what is ticked here.",
                interactive=True,
            )

        persona = gr.Textbox(
            value=(manifest.persona or "").strip(),
            label="persona · injected into every prompt",
            info="Leave unchanged to keep the cast's written identity.",
            lines=3,
            interactive=True,
        )

        with gr.Accordion("schedule · when this mind acts", open=False):
            with gr.Row():
                tick_every = gr.Number(
                    value=manifest.schedule.tick_every,
                    label="tick every (turns · blank = event-driven)",
                    precision=0,
                    minimum=0,
                    maximum=_TICK_MAX,
                    interactive=True,
                )
                max_consecutive = gr.Number(
                    value=manifest.schedule.max_consecutive,
                    label="max consecutive turns",
                    precision=0,
                    minimum=1,
                    maximum=_CONSECUTIVE_MAX,
                    interactive=True,
                )

    return AgentPanel(
        name=manifest.name,
        model=model,
        tools=tools_group,
        persona=persona,
        tick_every=tick_every,
        max_consecutive=max_consecutive,
    )
