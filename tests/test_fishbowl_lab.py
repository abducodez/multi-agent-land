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
from src.models import inference, modal_catalogue
from src.ui.fishbowl import lab

EXPECTED_HANDLE_KEYS = {
    "inference_backend",
    "scenario",
    "premise",
    "seed",
    "world",
    "narrator",
    "cast_models",
    "cast_tools",
    "cast_personas",
    "cast_schedules",
    "cast_roster",
    "judge_policy",
    "judge_model",
    "judge_strictness",
    "tools",
    "max_turns",
    "max_calls_per_turn",
    "max_total_tokens",
    "hourly_budget_usd",
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
    # The headline choice: a backend radio offering every registered backend.
    assert isinstance(handles["inference_backend"], gr.Radio)
    backend_values = {c[1] for c in handles["inference_backend"].choices}
    assert backend_values == {b.key for b in inference.backends()}
    assert handles["inference_backend"].value == inference.DEFAULT_BACKEND
    assert isinstance(handles["scenario"], gr.Radio)
    # The seed is an editable textbox (a preset dropdown fills it; the box is what Summon reads).
    assert isinstance(handles["seed"], gr.Textbox)
    assert isinstance(handles["narrator"], gr.Dropdown)
    # The cast picker is a gr.render writing into these states (one card per player).
    assert isinstance(handles["cast_models"], gr.State)
    assert isinstance(handles["cast_tools"], gr.State)
    assert isinstance(handles["cast_personas"], gr.State)
    assert isinstance(handles["cast_schedules"], gr.State)
    # Per-agent tool grants live on each card now; the legacy global handle is a State.
    assert isinstance(handles["tools"], gr.State)
    # The cast roster multiselect drives the effective cast (and Judge visibility).
    assert isinstance(handles["cast_roster"], gr.CheckboxGroup)
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


def test_model_choices_are_catalogue_keys_minus_ui_disabled():
    choices = lab.model_choices()  # defaults to the Modal backend (bare keys)
    keys = {key for _label, key in choices}
    # Every selectable value is a real catalogue endpoint key — nothing else is offerable.
    assert keys == set(_CATALOGUE_KEYS) - set(lab._DISABLED_MODELS)
    # UI-disabled models (e.g. gemma-4-26b) are never offered, even though they remain
    # in the catalogue for the engine.
    assert keys.isdisjoint(lab._DISABLED_MODELS)
    # Labels are human-readable and name the served model.
    assert all(" · " in label for label, _ in choices)


def test_model_choices_hf_backend_offers_qualified_hf_keys():
    choices = lab.model_choices("hf")
    keys = {key for _label, key in choices}
    assert keys, "the HF catalogue should offer at least one model"
    # Every HF key is backend-qualified and resolves on the HF backend (not Modal).
    assert all(key.startswith("hf:") for key in keys)
    assert all(inference.split_key(key)[0] == "hf" for key in keys)
    assert keys.isdisjoint(set(_CATALOGUE_KEYS))  # disjoint from the Modal keys


def test_collect_world_config_pins_hf_models_as_endpoints():
    registry = default_registry()
    scenario = registry.scenarios["thousand-token-wood"]
    worker = next(n for n in scenario.cast if registry.agents[n].role != "judge")
    judge = next(n for n in scenario.cast if registry.agents[n].role == "judge")
    hf_choices = lab.model_choices("hf")
    worker_key, judge_key = hf_choices[0][1], hf_choices[-1][1]

    world = lab.collect_world_config(
        scenario=scenario.title,
        premise="A new whimsical premise for the wood.",
        seed=scenario.default_seed,
        cast_models={worker: worker_key},
        judge_policy="Majority Vote",
        judge_model=judge_key,
        judge_strictness=60,
        tools=[],
        tokens=120_000,
        max_rounds=25,
        backend="hf",
    )

    by_name = {a.name: a for a in world.agents}
    # The HF-qualified keys are pinned verbatim and resolve to the HF backend.
    assert by_name[worker].model_endpoint == worker_key
    assert by_name[judge].model_endpoint == judge_key
    assert inference.split_key(worker_key)[0] == "hf"


def test_cast_defaults_cover_non_judge_cast_with_catalogue_keys():
    registry = default_registry()
    scenario = registry.scenarios["thousand-token-wood"]
    defaults = lab._cast_defaults(scenario)
    judge_names = {n for n in scenario.cast if (registry.agents.get(n) and registry.agents[n].role == "judge")}
    non_judge = [n for n in scenario.cast if n not in judge_names]
    assert set(defaults) == set(non_judge)  # judge excluded (set under §04)
    assert all(v in _CATALOGUE_KEYS for v in defaults.values())


def test_merge_roster_model_defaults_seeds_added_agent_and_keeps_picks():
    """A mind imported from another scenario gets a default model; prior picks survive."""
    registry = default_registry()
    scenario = registry.scenarios["thousand-token-wood"]
    # An agent the registry knows but this scenario's cast does not include.
    outsider = next(n for n, m in sorted(registry.agents.items()) if n not in scenario.cast and m.role != "judge")
    existing_worker = next(n for n in scenario.cast if registry.agents[n].role != "judge")
    roster = list(scenario.cast) + [outsider]
    pinned = {existing_worker: _CATALOGUE_KEYS[-1]}

    merged = lab._merge_roster_model_defaults(scenario, roster, pinned)

    # The newly-added mind now has a real catalogue key — Summon won't drop its model.
    assert outsider in merged
    assert merged[outsider] in _CATALOGUE_KEYS
    # The user's existing selection is preserved untouched.
    assert merged[existing_worker] == _CATALOGUE_KEYS[-1]
    # The judge is excluded (it is bound under §04, not the cast picker).
    judge = next((n for n in roster if registry.agents.get(n) and registry.agents[n].role == "judge"), None)
    assert judge not in merged


def test_added_agent_from_another_scenario_runs_with_its_model():
    """End-to-end: an imported mind, once seeded, is pinned in the assembled run."""
    registry = default_registry()
    scenario = registry.scenarios["thousand-token-wood"]
    outsider = next(n for n, m in sorted(registry.agents.items()) if n not in scenario.cast and m.role != "judge")
    roster = list(scenario.cast) + [outsider]
    # Seed the way the cast_roster.change handler does, starting from an empty state.
    cast_models = lab._merge_roster_model_defaults(scenario, roster, {})

    world = lab.collect_world_config(
        scenario=scenario.title,
        premise=scenario.goal,
        seed=scenario.default_seed,
        cast_models=cast_models,
        judge_policy="Majority Vote",
        judge_model=_CATALOGUE_KEYS[-1],
        judge_strictness=50,
        tools=[],
        tokens=120_000,
        max_rounds=25,
        cast_roster=roster,
    )

    by_name = {a.name: a for a in world.agents}
    assert outsider in by_name
    assert by_name[outsider].model_endpoint in _CATALOGUE_KEYS
    assert registry.agents[outsider].model_endpoint is None  # registry untouched


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


# ── the editable surface: per-agent tools / persona / schedule + scenario knobs ──


def test_collect_world_config_pins_tool_persona_schedule_onto_the_right_agent():
    registry = default_registry()
    scenario = registry.scenarios["oracle-grove"]
    # The fortune-teller is the tool-capable mind; scene-whisperer has no grant.
    world = lab.collect_world_config(
        scenario=scenario.name,
        premise="",
        seed="",
        cast_models={},
        judge_policy="Majority Vote",
        judge_model="",
        judge_strictness=50,
        tools=[],
        tokens=None,
        max_rounds=None,
        cast_tools={"fortune-teller": ["oracle"], "scene-whisperer": []},
        cast_personas={"scene-whisperer": "A brand-new whispered identity for the test."},
        cast_schedules={"fortune-teller": {"tick_every": 3, "max_consecutive": 2}},
    )

    by_name = {a.name: a for a in world.agents}
    assert by_name["fortune-teller"].tools == ["oracle"]
    assert by_name["scene-whisperer"].tools == []  # explicitly dropped grant
    assert by_name["scene-whisperer"].persona == "A brand-new whispered identity for the test."
    assert by_name["fortune-teller"].schedule.tick_every == 3
    assert by_name["fortune-teller"].schedule.max_consecutive == 2
    # The shared registry manifests are untouched (non-destructive model_copy).
    assert registry.agents["scene-whisperer"].persona != "A brand-new whispered identity for the test."
    assert registry.agents["fortune-teller"].schedule.tick_every == 1


def test_collect_world_config_drops_ungranted_or_unknown_tools():
    registry = default_registry()
    scenario = registry.scenarios["oracle-grove"]
    world = lab.collect_world_config(
        scenario=scenario.name,
        premise="",
        seed="",
        cast_models={},
        judge_policy="Majority Vote",
        judge_model="",
        judge_strictness=50,
        tools=[],
        tokens=None,
        max_rounds=None,
        # tts.speak / dice.roll are friendly labels the engine does not actually have.
        cast_tools={"fortune-teller": ["oracle", "tts.speak", "dice.roll"]},
    )
    by_name = {a.name: a for a in world.agents}
    assert by_name["fortune-teller"].tools == ["oracle"]  # only the real, engine-backed tool


def test_collect_world_config_never_escalates_a_non_tool_agent():
    # The UI never offers scene-whisperer a tool picker (it has no grant), but a stale or
    # crafted cast_tools entry must NOT be able to grant it a capability it was never given.
    registry = default_registry()
    scenario = registry.scenarios["oracle-grove"]
    assert registry.agents["scene-whisperer"].tools == []  # guard: it is genuinely tool-less
    world = lab.collect_world_config(
        scenario=scenario.name,
        premise="",
        seed="",
        cast_models={},
        judge_policy="Majority Vote",
        judge_model="",
        judge_strictness=50,
        tools=[],
        tokens=None,
        max_rounds=None,
        cast_tools={"scene-whisperer": ["oracle"]},  # crafted escalation attempt
    )
    by_name = {a.name: a for a in world.agents}
    assert by_name["scene-whisperer"].tools == []  # intersected with its own (empty) grant


def test_collect_world_config_honours_roster_genesis_and_governor():
    registry = default_registry()
    scenario = registry.scenarios["thousand-token-wood"]
    judge = next(n for n in scenario.cast if registry.agents[n].role == "judge")
    trimmed = [n for n in scenario.cast if n != judge]  # drop the judge from the roster

    world = lab.collect_world_config(
        scenario=scenario.name,
        premise="",
        seed="",
        cast_models={},
        judge_policy="Majority Vote",
        judge_model="",
        judge_strictness=50,
        tools=[],
        tokens=None,
        max_rounds=None,
        cast_roster=trimmed,
        genesis="A custom genesis the test pins in.",
        max_turns=17,
        max_calls_per_turn=5,
        max_total_tokens=44_000,
        hourly_budget_usd=2.5,
    )

    out = world.scenarios[0]
    # The roster override drives the cast (judge removed → a judge-less, valid world).
    assert out.cast == trimmed
    assert judge not in {a.name for a in world.agents}
    assert out.genesis_text == "A custom genesis the test pins in."
    assert out.governor is not None
    assert out.governor.max_turns == 17
    assert out.governor.max_calls_per_turn == 5
    assert out.governor.max_total_tokens == 44_000
    assert out.governor.hourly_budget_usd == 2.5


def test_collect_world_config_judgeless_world_is_valid_without_judge_knobs():
    # oracle-grove is a judge-less tool-use showcase; composing it with no judge model
    # must still validate. (Open Table gained a table-judge with the arena verdict.)
    registry = default_registry()
    scenario = registry.scenarios["oracle-grove"]
    world = lab.collect_world_config(
        scenario=scenario.name,
        premise="",
        seed="",
        cast_models={},
        judge_policy="Majority Vote",
        judge_model="",  # no judge to bind
        judge_strictness=50,
        tools=[],
        tokens=None,
        max_rounds=None,
    )
    assert isinstance(world, WorldConfig)
    assert all(a.role != "judge" for a in world.agents)
    assert world.scenarios[0].cast == list(scenario.cast)


def test_collect_world_config_legacy_token_and_round_knobs_still_apply():
    # Back-compat: callers passing the old tokens / max_rounds get a governor too.
    registry = default_registry()
    scenario = registry.scenarios["thousand-token-wood"]
    world = lab.collect_world_config(
        scenario=scenario.name,
        premise="",
        seed="",
        cast_models={},
        judge_policy="Majority Vote",
        judge_model="",
        judge_strictness=50,
        tools=[],
        tokens=99_000,
        max_rounds=33,
    )
    gov = world.scenarios[0].governor
    assert gov is not None
    assert gov.max_turns == 33
    assert gov.max_total_tokens == 99_000
