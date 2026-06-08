"""Fishbowl · The Lab — the Gradio composer that turns knobs into a runnable world.

This is the left half of the two-tab theater: a form that mirrors ``ui/raw/lab.jsx``'s
six sections (Scenario & Goal, The Cast, The Judge, The Initiator, Tools, Run & Budget)
plus the sticky launch bar (narrator selector + Summon / Surprise me).

Two surfaces live here, kept deliberately separate:

  * :func:`build_lab` builds the Gradio component *tree* inside a caller's ``gr.Blocks``
    and returns a ``dict`` of handles.  It wires *no* callbacks — the app shell (Unit 9)
    owns the Summon button and the session.  This module never imports sibling render or
    show modules, so the composer stays independent of the live stage.

  * :func:`collect_world_config` is a *pure* helper that assembles a per-run, WorldConfig
    -shaped dict from the form values and validates it with ``validate_world`` /
    ``validate_scenario`` (``src/core/config``).  It never mutates registry state — it
    reads the registry's manifests and rebuilds a fresh, self-contained world so Unit 9
    can construct a Conductor from a composed run.  See ADR-0011 / ADR-0021.
"""

from __future__ import annotations

import gradio as gr

from src.core.config import ScenarioConfig, validate_scenario, validate_world
from src.core.registry import default_registry
from src.ui.fishbowl.adapter import VOICES, scenario_voice

# ── design vocabulary (mirrors ui/raw/lab.jsx) ──────────────────────────────────

# Verdict policy presets — presentation labels; the engine maps them to a judge.
JUDGE_POLICIES: list[str] = [
    "Majority Vote",
    "Consensus Myth",
    "Beyond Reasonable Doubt",
    "Last Mind Standing",
    "Judge's Whim",
]

# Logical model profiles the engine understands (manifest.ModelProfile).
MODEL_PROFILES: list[str] = ["tiny", "fast", "balanced", "strong"]

# MCP tool grants the cast may reach for (friendly label, stored id).  Mirrors
# ui/raw/lab.jsx:MCP_TOOLS; the value is what we store on the run.
TOOL_CHOICES: list[tuple[str, str]] = [
    ("dice.roll · randomness source", "dice.roll"),
    ("vote.tally · count the room", "vote.tally"),
    ("lore.append · write to canon", "lore.append"),
    ("mood.read · sense the table", "mood.read"),
    ("oracle · ask the unseen", "oracle"),
    ("tts.speak · give it a voice", "tts.speak"),
]

# Cast Dataframe columns (editable in the Lab).
CAST_COLUMNS: list[str] = ["name", "archetype", "model_profile", "temp"]

# The scenario we lead with — the hackathon's north-star world.
_PREFERRED_SCENARIO = "thousand-token-wood"


# ── data sourcing (read-only over the registry) ─────────────────────────────────


def _ordered_scenarios() -> list[ScenarioConfig]:
    """Registry scenarios, preferred world first, then the rest alphabetically."""
    registry = default_registry()
    scenarios = list(registry.scenarios.values())
    scenarios.sort(key=lambda s: (s.name != _PREFERRED_SCENARIO, s.title or s.name))
    return scenarios


def _scenario_by_title(title: str) -> ScenarioConfig | None:
    """Resolve a scenario by its display title (what the Radio shows)."""
    for scenario in _ordered_scenarios():
        if (scenario.title or scenario.name) == title:
            return scenario
    return None


def _cast_rows_for(scenario: ScenarioConfig) -> list[list]:
    """Seed editable cast rows from a scenario's cast manifests.

    Each row is ``[name, archetype, model_profile, temp]``.  The archetype and
    model_profile come straight from the agent manifest; temp defaults to 0.8 (the
    model default) so the column is meaningful but editable.
    """
    registry = default_registry()
    rows: list[list] = []
    for agent_name in scenario.cast:
        manifest = registry.agents.get(agent_name)
        if manifest is None:
            # A scenario referencing an unknown agent shouldn't happen (validate_world
            # guards it) but degrade gracefully rather than crash the form.
            rows.append([agent_name, f"the {agent_name}", "fast", 0.8])
            continue
        archetype = manifest.archetype or f"the {manifest.role}"
        rows.append([manifest.name, archetype, manifest.model_profile, 0.8])
    return rows


def _voice_choices() -> list[tuple[str, str]]:
    """Narrator dropdown choices: (friendly label, voice id) for the four voices."""
    return [(f"{name} · {desc}", voice_id) for voice_id, (name, desc) in VOICES.items()]


# ── the component tree ───────────────────────────────────────────────────────────


def build_lab() -> dict[str, gr.components.Component]:
    """Build the Lab composer's Gradio tree and return its handles.

    Called inside the caller's ``with gr.Blocks(): gr.Tab("The Lab"):`` block.
    Wires no callbacks and imports no sibling render/show module — the app shell
    (Unit 9) binds ``summon_btn`` to the session.  Returns a dict of every handle a
    caller needs to read the composed run (keys: ``scenario, premise, seed, world,
    narrator, cast, judge_policy, judge_model, judge_strictness, tools, tokens,
    max_rounds, seed_num, cadence, summon_btn, surprise_btn``).
    """
    scenarios = _ordered_scenarios()
    first = scenarios[0]
    titles = [s.title or s.name for s in scenarios]

    handles: dict[str, gr.components.Component] = {}

    gr.Markdown(
        "### The Lab · compose the experiment\n"
        "Build a bowl of minds, then let them perform. Every knob feeds one durable "
        "ledger — press **Summon** and the conductor seeds the world."
    )

    # 01 — Scenario & Goal
    with gr.Group():
        gr.Markdown("**01 · Scenario & Goal**")
        handles["scenario"] = gr.Radio(
            choices=titles,
            value=titles[0],
            label="Scenario",
            info="The world the cast wakes up in.",
        )
        handles["premise"] = gr.Textbox(
            value=first.goal,
            label="Premise / goal",
            info="Leave blank to use the scenario's own goal.",
            lines=3,
        )

    # 02 — The Initiator (seeds + pre-loaded world state)
    with gr.Group():
        gr.Markdown("**02 · The Initiator** — the opening beat + facts the minds wake knowing")
        handles["seed"] = gr.Dropdown(
            choices=first.example_seeds or [first.default_seed],
            value=first.default_seed,
            label="Seed event",
            allow_custom_value=True,
            info="The first beat the conductor writes into the ledger.",
        )
        handles["world"] = gr.Textbox(
            value=first.genesis_text or "",
            label="Pre-loaded world state",
            lines=2,
        )

    # 03 — The Cast (editable Dataframe)
    with gr.Group():
        gr.Markdown(
            "**03 · The Cast** — bind any mind to any model "
            "(profile: tiny / fast / balanced / strong); watch the expensive one play"
        )
        handles["cast"] = gr.Dataframe(
            value=_cast_rows_for(first),
            headers=CAST_COLUMNS,
            datatype=["str", "str", "str", "number"],
            column_count=len(CAST_COLUMNS),
            # The grid re-seeds (name/archetype/model) when the scenario changes; it shows
            # the player roster — a scenario's judge/host is configured under §04, mirroring
            # the prototype's 4-player spy cast (ui/raw/data.js).
            row_count=len(first.cast),
            interactive=True,
            label="Cast",
        )

    # 04 — The Judge + 05 — Tools (side by side)
    with gr.Row():
        with gr.Group():
            gr.Markdown("**04 · The Judge**")
            handles["judge_policy"] = gr.Dropdown(
                choices=JUDGE_POLICIES,
                value=JUDGE_POLICIES[0],
                label="Policy preset",
            )
            handles["judge_model"] = gr.Dropdown(
                choices=MODEL_PROFILES,
                value="strong",
                label="Bound model profile",
            )
            handles["judge_strictness"] = gr.Slider(
                minimum=0,
                maximum=100,
                value=50,
                step=1,
                label="Strictness (lenient → merciless)",
            )
        with gr.Group():
            gr.Markdown("**05 · Tools** — MCP servers the minds may reach for")
            handles["tools"] = gr.CheckboxGroup(
                choices=TOOL_CHOICES,
                value=[],
                label="Tools",
            )

    # 06 — Run & Budget
    with gr.Group():
        gr.Markdown("**06 · Run & Budget** — a fixed seed + recorded outputs reproduce a run exactly")
        with gr.Row():
            handles["tokens"] = gr.Number(value=200_000, label="Token ceiling", precision=0)
            handles["max_rounds"] = gr.Number(value=40, label="Max rounds", precision=0)
            handles["seed_num"] = gr.Number(value=7, label="Random seed", precision=0)
        handles["cadence"] = gr.Slider(
            minimum=0,
            maximum=2,
            value=1,
            step=1,
            label="Tick cadence (live · one move → fast-forward)",
        )

    # Sticky launch bar — narrator + Summon / Surprise me
    with gr.Row():
        handles["narrator"] = gr.Dropdown(
            choices=_voice_choices(),
            value=scenario_voice(first.name),
            label="Narrator",
        )
        handles["surprise_btn"] = gr.Button("Surprise me")
        handles["summon_btn"] = gr.Button("Summon the bowl", variant="primary")

    return handles


# ── pure config assembly (the 'configure a run' surface) ─────────────────────────


def collect_world_config(
    *,
    scenario: str,
    premise: str,
    seed: str,
    cast_rows: list,
    judge_policy: str,
    judge_model: str,
    judge_strictness: float,
    tools: list[str],
    tokens: float | int | None,
    max_rounds: float | int | None,
):
    """Assemble + validate a per-run world from the Lab's form values.

    Returns the validated :class:`WorldConfig` (raising ``pydantic.ValidationError``
    on an incoherent run).  This is the bridge Unit 9 uses to build a Conductor from a
    composed run.

    The base scenario (selected by its display *title*) supplies the cast roster and
    the agent manifests; the edited ``cast_rows`` (``[name, archetype, model_profile,
    temp]``) override each agent's ``model_profile`` and ``archetype`` non-destructively
    via :meth:`pydantic.BaseModel.model_copy`.  The premise overrides the scenario goal,
    ``seed`` becomes its ``default_seed``, and the budget knobs feed the governor.

    The judge knobs (``judge_policy`` / ``judge_model`` / ``judge_strictness``) and the
    ``tools`` grant are accepted and shape-checked, but the deeper synthesis (turning a
    policy preset into a judge AgentManifest, wiring tool grants onto worker manifests) is
    deferred to the engine and TODO'd below — every dict this builds is still passed
    through ``validate_scenario`` / ``validate_world`` before return, so the contract
    ("emit, validate, run") holds. See ADR-0011.

    TODO(unit-9): map ``judge_policy`` / ``judge_model`` to a concrete judge AgentManifest
    and wire the selected ``tools`` onto each worker's manifest ``tools`` grant once the
    judge/tool contracts land in the live path.
    """
    registry = default_registry()
    base = _scenario_by_title(scenario)
    if base is None:
        # Fall back to a name match so callers may pass either title or name.
        base = registry.scenarios.get(scenario)
    if base is None:
        raise ValueError(f"unknown scenario {scenario!r} (have: {sorted(registry.scenarios)})")

    # Edits per cast row → manifest overrides, keyed by name.  Non-destructive: we
    # model_copy each manifest rather than mutating the cached registry instance.
    edits: dict[str, dict] = {}
    for row in cast_rows or []:
        if not row or row[0] in (None, ""):
            continue
        name = str(row[0]).strip()
        patch: dict = {}
        if len(row) > 1 and row[1] not in (None, ""):
            patch["archetype"] = str(row[1])
        if len(row) > 2 and str(row[2]).strip() in MODEL_PROFILES:
            patch["model_profile"] = str(row[2]).strip()
        edits[name] = patch

    # Build the per-run cast: every manifest the scenario references, with overrides.
    agents = []
    cast_names: list[str] = []
    for agent_name in base.cast:
        manifest = registry.agents.get(agent_name)
        if manifest is None:
            raise ValueError(f"scenario {base.name!r} references undefined agent {agent_name!r}")
        manifest = manifest.model_copy(update=edits.get(agent_name, {}))
        agents.append(manifest.model_dump(mode="python"))
        cast_names.append(manifest.name)

    # Per-run scenario: premise overrides goal, chosen seed becomes the default.
    scenario_dict = {
        "name": base.name,
        "title": base.title,
        "goal": (premise or "").strip() or base.goal,
        "default_seed": (seed or "").strip() or base.default_seed,
        "example_seeds": list(base.example_seeds),
        "cast": cast_names,
        "genesis_text": base.genesis_text,
    }

    # Governor budget from the run knobs (None/blank → omit so defaults apply).
    governor: dict = {}
    if max_rounds:
        governor["max_turns"] = int(max_rounds)
    if tokens:
        governor["max_total_tokens"] = int(tokens)
    if governor:
        scenario_dict["governor"] = dict(governor)

    # Validate the scenario slice on its own (the per-scenario contract).
    validate_scenario(scenario_dict)

    world_dict = {
        "agents": agents,
        "scenarios": [scenario_dict],
    }
    if governor:
        world_dict["governor"] = governor

    return validate_world(world_dict)


# ── e2e harness (not committed as a standalone entrypoint) ───────────────────────

if __name__ == "__main__":
    with gr.Blocks(title="Fishbowl · The Lab") as demo:
        with gr.Tab("The Lab"):
            build_lab()
    demo.launch()
