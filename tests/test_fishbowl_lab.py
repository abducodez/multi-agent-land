"""Mock-free tests for the Fishbowl Lab composer (Unit 8).

Cover both surfaces: ``build_lab`` returns the expected handles inside a Blocks, and
``collect_world_config`` assembles a real scenario's data into a validated WorldConfig
(round-tripping through ``validate_world`` / ``validate_scenario``) without mutating the
shared registry.  Model selection is constrained to the Modal catalogue and pins each
agent's ``model_endpoint`` (ADR-0022).
"""

from __future__ import annotations

import gradio as gr
import pytest

from src.core.config import WorldConfig
from src.core.registry import default_registry
from src.models import modal_catalogue
from src.ui.fishbowl import lab

EXPECTED_HANDLE_KEYS = {
    "scenario",
    "premise",
    "seed",
    "world",
    "narrator",
    "cast_models",
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

# A couple of real catalogue keys to cast (the catalogue loads offline — a plain file).
_CATALOGUE_KEYS = [e["key"] for e in modal_catalogue.entries()]


def test_build_lab_returns_expected_handles():
    with gr.Blocks():
        with gr.Tab("The Lab"):
            handles = lab.build_lab()

    assert set(handles) == EXPECTED_HANDLE_KEYS
    assert isinstance(handles["scenario"], gr.Radio)
    assert isinstance(handles["seed"], gr.Dropdown)
    assert handles["seed"].allow_custom_value is True
    assert isinstance(handles["narrator"], gr.Dropdown)
    # The cast picker is a gr.render writing into this state (one dropdown per player).
    assert isinstance(handles["cast_models"], gr.State)
    assert isinstance(handles["tools"], gr.CheckboxGroup)
    assert isinstance(handles["judge_strictness"], gr.Slider)
    assert isinstance(handles["summon_btn"], gr.Button)
    assert isinstance(handles["surprise_btn"], gr.Button)


def test_judge_model_dropdown_offers_only_catalogue_models():
    with gr.Blocks():
        handles = lab.build_lab()
    assert isinstance(handles["judge_model"], gr.Dropdown)
    values = {c[1] for c in handles["judge_model"].choices}
    assert values <= set(_CATALOGUE_KEYS)
    assert values, "judge model dropdown should list the catalogue"


def test_model_choices_are_all_catalogue_keys():
    choices = lab.model_choices()
    # Every selectable value is a real catalogue endpoint key — nothing else is offerable.
    assert {key for _label, key in choices} == set(_CATALOGUE_KEYS)
    # Labels are human-readable and name the served model.
    assert all(" · " in label for label, _ in choices)


def test_cast_defaults_cover_non_judge_cast_with_catalogue_keys():
    registry = default_registry()
    scenario = registry.scenarios["thousand-token-wood"]
    defaults = lab._cast_defaults(scenario)
    judge_names = {n for n in scenario.cast if (registry.agents.get(n) and registry.agents[n].role == "judge")}
    non_judge = [n for n in scenario.cast if n not in judge_names]
    assert set(defaults) == set(non_judge)  # judge excluded (set under §04)
    assert all(v in _CATALOGUE_KEYS for v in defaults.values())


def test_collect_world_config_pins_selected_models_as_endpoints():
    registry = default_registry()
    scenario = registry.scenarios["thousand-token-wood"]
    worker = next(n for n in scenario.cast if registry.agents[n].role != "judge")
    judge = next(n for n in scenario.cast if registry.agents[n].role == "judge")
    worker_key, judge_key = _CATALOGUE_KEYS[0], _CATALOGUE_KEYS[-1]

    world = lab.collect_world_config(
        scenario=scenario.title,
        premise="A new whimsical premise for the wood.",
        seed=scenario.default_seed,
        cast_models={worker: worker_key},
        judge_policy="Majority Vote",
        judge_model=judge_key,
        judge_strictness=60,
        tools=["dice.roll", "vote.tally"],
        tokens=120_000,
        max_rounds=25,
    )

    assert isinstance(world, WorldConfig)
    by_name = {a.name: a for a in world.agents}
    assert by_name[worker].model_endpoint == worker_key
    assert by_name[judge].model_endpoint == judge_key  # §04 binds the judge
    # registry untouched (non-destructive model_copy)
    assert registry.agents[worker].model_endpoint is None
    assert registry.agents[judge].model_endpoint is None

    out = world.scenarios[0]
    assert out.name == scenario.name
    assert out.goal == "A new whimsical premise for the wood."
    assert out.cast == list(scenario.cast)
    assert out.governor is not None
    assert out.governor.max_turns == 25
    assert out.governor.max_total_tokens == 120_000


def test_collect_world_config_ignores_unknown_or_blank_model_keys():
    registry = default_registry()
    scenario = registry.scenarios["thousand-token-wood"]
    worker = next(n for n in scenario.cast if registry.agents[n].role != "judge")

    world = lab.collect_world_config(
        scenario=scenario.name,  # also accept name, not just title
        premise="",
        seed="",
        cast_models={worker: "not-a-real-endpoint"},  # bogus key → ignored
        judge_policy="Judge's Whim",
        judge_model="",  # blank → judge keeps its tier
        judge_strictness=10,
        tools=[],
        tokens=None,
        max_rounds=None,
    )

    by_name = {a.name: a for a in world.agents}
    assert by_name[worker].model_endpoint is None  # stale/unknown key dropped
    # blank premise/seed fall back to the scenario's own
    assert world.scenarios[0].goal == scenario.goal
    assert world.scenarios[0].default_seed == scenario.default_seed


def test_collect_world_config_unknown_scenario_raises():
    with pytest.raises(ValueError, match="unknown scenario"):
        lab.collect_world_config(
            scenario="not-a-real-world",
            premise="",
            seed="",
            cast_models={},
            judge_policy="Majority Vote",
            judge_model="",
            judge_strictness=50,
            tools=[],
            tokens=None,
            max_rounds=None,
        )
