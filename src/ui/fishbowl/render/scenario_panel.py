"""Fishbowl · Lab — the scenario-level editable controls.

A factory for the run's *scenario* knobs (as opposed to the per-agent cards in
``agent_panel``): the shared goal, the opening seed, the pre-loaded genesis text, the
cast roster (a multiselect over the whole registry, defaulting to the scenario's own
cast), and the governor budget bounds the engine enforces (``max_turns`` /
``max_calls_per_turn`` / ``max_total_tokens`` / ``hourly_budget_usd``).

Like ``agent_panel`` it wires nothing — it returns the component handles so
:func:`build_lab` can read them on Summon and re-seed them when the scenario changes.
Everything it produces flows through ``collect_world_config`` →
``validate_scenario`` / ``validate_world`` (ADR-0011 / ADR-0025).
"""

from __future__ import annotations

from dataclasses import dataclass

import gradio as gr

from src.core.config import GovernorConfig, ScenarioConfig


@dataclass
class ScenarioPanel:
    """Handles for the scenario-level controls :func:`build_lab` reads on Summon.

    The opening *seed* is composed separately (it lives in the always-visible Quick lane,
    not in this advanced panel), so it is not one of these handles.
    """

    premise: gr.Textbox
    world: gr.Textbox
    cast_roster: gr.CheckboxGroup
    max_turns: gr.Number
    max_calls_per_turn: gr.Number
    max_total_tokens: gr.Number
    hourly_budget_usd: gr.Number


def _roster_choices(available: list) -> list[tuple[str, str]]:
    """(friendly label, agent name) for every registry agent the roster may include."""
    choices: list[tuple[str, str]] = []
    for manifest in available:
        arche = manifest.archetype or f"the {manifest.role}"
        choices.append((f"{manifest.name} · {arche}", manifest.name))
    return choices


def render_scenario_panel(
    scenario: ScenarioConfig,
    *,
    available_agents: list,
    cast_value: list[str] | None = None,
) -> ScenarioPanel:
    """Render the scenario-level controls and return their handles.

    *available_agents* is the full registry roster (the multiselect's choices);
    *cast_value* seeds the multiselect (defaults to the scenario's own cast).  The
    governor knobs seed from the scenario's ``governor`` block, falling back to
    :class:`GovernorConfig` defaults so a scenario without an explicit budget still
    shows sensible numbers.  The opening seed is *not* built here — it lives in the
    always-visible Quick lane (see :func:`build_lab`).
    """
    gov = scenario.governor or GovernorConfig()
    roster = list(cast_value) if cast_value is not None else list(scenario.cast)

    with gr.Group():
        gr.Markdown("**Goal & genesis** — what the whole cast is reaching for, and the world they wake into")
        premise = gr.Dropdown(
            choices=[scenario.goal] if scenario.goal else [],
            value=scenario.goal,
            label="Premise / goal",
            info="The shared objective handed to the whole cast — keep the scenario's goal or type your own.",
            allow_custom_value=True,  # a dropdown that also supports free text
        )
        world = gr.Textbox(
            value=scenario.genesis_text or "",
            label="Pre-loaded world state",
            lines=2,
        )
        cast_roster = gr.CheckboxGroup(
            choices=_roster_choices(available_agents),
            value=roster,
            label="Cast roster — who wakes up in this world",
            info="Add or drop minds; dropping the judge hides the Judge section.",
        )

    with gr.Group():
        gr.Markdown("**Budget** — the bounds the governor enforces (a fixed seed + these reproduce a run exactly)")
        with gr.Row():
            max_turns = gr.Number(value=gov.max_turns, label="Max turns", precision=0, minimum=1)
            max_calls_per_turn = gr.Number(
                value=gov.max_calls_per_turn, label="Max calls / turn", precision=0, minimum=1
            )
        with gr.Row():
            max_total_tokens = gr.Number(
                value=gov.max_total_tokens, label="Token ceiling (blank = unbounded)", precision=0, minimum=1
            )
            hourly_budget_usd = gr.Number(
                value=gov.hourly_budget_usd, label="Hourly budget (USD · blank = none)", minimum=0
            )

    return ScenarioPanel(
        premise=premise,
        world=world,
        cast_roster=cast_roster,
        max_turns=max_turns,
        max_calls_per_turn=max_calls_per_turn,
        max_total_tokens=max_total_tokens,
        hourly_budget_usd=hourly_budget_usd,
    )
