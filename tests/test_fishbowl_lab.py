"""Mock-free tests for the Fishbowl Lab composer (Unit 8).

Cover both surfaces: ``build_lab`` returns the expected handles inside a Blocks, and
``collect_world_config`` assembles a real scenario's data into a validated WorldConfig
(round-tripping through ``validate_world`` / ``validate_scenario``) without mutating the
shared registry.
"""

from __future__ import annotations

import gradio as gr
import pytest

from src.core.config import WorldConfig
from src.core.registry import default_registry
from src.ui.fishbowl import lab

EXPECTED_HANDLE_KEYS = {
    "scenario",
    "premise",
    "seed",
    "world",
    "narrator",
    "cast",
    "judge_policy",
    "judge_model",
    "judge_strictness",
    "tools",
    "tokens",
    "max_rounds",
    "seed_num",
    "cadence",
    "summon_btn",
    "surprise_btn",
}


def test_build_lab_returns_expected_handles():
    with gr.Blocks():
        with gr.Tab("The Lab"):
            handles = lab.build_lab()

    assert set(handles) == EXPECTED_HANDLE_KEYS
    assert isinstance(handles["scenario"], gr.Radio)
    assert isinstance(handles["seed"], gr.Dropdown)
    assert handles["seed"].allow_custom_value is True
    assert isinstance(handles["narrator"], gr.Dropdown)
    assert isinstance(handles["cast"], gr.Dataframe)
    assert handles["cast"].interactive is True
    assert isinstance(handles["tools"], gr.CheckboxGroup)
    assert isinstance(handles["judge_strictness"], gr.Slider)
    assert isinstance(handles["summon_btn"], gr.Button)
    assert isinstance(handles["surprise_btn"], gr.Button)


def test_build_lab_radio_lists_real_scenarios():
    registry = default_registry()
    real_titles = {s.title or s.name for s in registry.scenarios.values()}
    with gr.Blocks():
        handles = lab.build_lab()
    radio_choices = {c[0] for c in handles["scenario"].choices}
    assert radio_choices == real_titles


def test_build_lab_cast_seeded_from_scenario():
    with gr.Blocks():
        handles = lab.build_lab()
    rows = handles["cast"].value["data"]
    assert rows, "cast should be seeded with at least one row"
    profiles = {row[2] for row in rows}
    assert profiles <= set(lab.MODEL_PROFILES)


def test_collect_world_config_validates_real_scenario():
    registry = default_registry()
    scenario = registry.scenarios["thousand-token-wood"]
    cast_rows = lab._cast_rows_for(scenario)

    world = lab.collect_world_config(
        scenario=scenario.title,
        premise="A new whimsical premise for the wood.",
        seed=scenario.default_seed,
        cast_rows=cast_rows,
        judge_policy="Majority Vote",
        judge_model="strong",
        judge_strictness=60,
        tools=["dice.roll", "vote.tally"],
        tokens=120_000,
        max_rounds=25,
    )

    assert isinstance(world, WorldConfig)
    assert len(world.scenarios) == 1
    out = world.scenarios[0]
    assert out.name == scenario.name
    assert out.goal == "A new whimsical premise for the wood."
    assert out.cast == list(scenario.cast)
    # cross-reference check: every cast name resolves to a defined agent
    assert {a.name for a in world.agents} >= set(out.cast)
    assert out.governor is not None
    assert out.governor.max_turns == 25
    assert out.governor.max_total_tokens == 120_000


def test_collect_world_config_applies_cast_edits_nondestructively():
    registry = default_registry()
    scenario = registry.scenarios["thousand-token-wood"]
    first_agent = scenario.cast[0]
    original_profile = registry.agents[first_agent].model_profile

    rows = lab._cast_rows_for(scenario)
    # flip the first agent's profile in the edited rows
    new_profile = "strong" if original_profile != "strong" else "tiny"
    rows[0][2] = new_profile

    world = lab.collect_world_config(
        scenario=scenario.name,  # also accept name, not just title
        premise="",
        seed="",
        cast_rows=rows,
        judge_policy="Judge's Whim",
        judge_model="balanced",
        judge_strictness=10,
        tools=[],
        tokens=None,
        max_rounds=None,
    )

    edited = next(a for a in world.agents if a.name == first_agent)
    assert edited.model_profile == new_profile
    # registry untouched
    assert registry.agents[first_agent].model_profile == original_profile
    # blank premise/seed fall back to the scenario's own
    assert world.scenarios[0].goal == scenario.goal
    assert world.scenarios[0].default_seed == scenario.default_seed


def test_collect_world_config_unknown_scenario_raises():
    with pytest.raises(ValueError, match="unknown scenario"):
        lab.collect_world_config(
            scenario="not-a-real-world",
            premise="",
            seed="",
            cast_rows=[],
            judge_policy="Majority Vote",
            judge_model="fast",
            judge_strictness=50,
            tools=[],
            tokens=None,
            max_rounds=None,
        )
