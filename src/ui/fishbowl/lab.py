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
from src.core.manifest import AgentManifest
from src.core.registry import default_registry
from src.models import inference
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


def _resolve_scenario(value: str) -> ScenarioConfig | None:
    """Resolve a scenario by display *title* or internal name — the two forms the Radio
    and the app shell may pass — or None.  One lookup rule, used everywhere."""
    return _scenario_by_title(value) or default_registry().scenarios.get(value)


def backend_choices() -> list[tuple[str, str]]:
    """Radio choices for the inference backend: ``(friendly label, backend key)``.

    The two ways the cast can think: **Modal** (self-hosted vLLM you deploy) and
    **Hugging Face** (serverless Inference Providers — many small models, just a token).
    The selected backend decides which catalogue the cast/judge pickers draw from."""
    return [(f"{b.label} · {b.blurb}", b.key) for b in inference.backends()]


def model_choices(backend: str = inference.DEFAULT_BACKEND) -> list[tuple[str, str]]:
    """Dropdown choices for *backend*'s model catalogue: ``(friendly label, qualified key)``.

    The catalogue is the single source of truth (``modal/catalogue.py`` for Modal,
    ``src/models/hf_catalogue.py`` for Hugging Face), read through the unified
    ``inference`` registry, so the Lab can *only* offer models that backend can actually
    run.  Both catalogues are plain stdlib data, so the picker is populated offline (no
    token needed to browse).  Empty list → a stripped deployment with no catalogue, in
    which case the cast falls back to the deterministic stub.  The stored value is the
    backend-qualified key (``hf:<repo>`` for HF; a bare slug for Modal)."""
    choices: list[tuple[str, str]] = []
    for entry in inference.entries(backend):
        served = entry["served_model_id"].split("/")[-1]
        params = f"{entry['params_b']:g}B" if entry.get("params_b") else "?"
        tier = entry["profile"] or "specialist"
        provider = entry["provider"]
        if not any(c.isupper() for c in provider):
            provider = provider.title()  # tidy bare lowercase keys (e.g. "nvidia")
        choices.append((f"{served} · {params} · {tier} · {provider}", entry["key"]))
    return choices


def _default_model_key(manifest: AgentManifest, backend: str = inference.DEFAULT_BACKEND) -> str | None:
    """Qualified key a cast row defaults to, on *backend*: the manifest's explicit
    ``model_endpoint`` (only honoured on its own backend — Modal), else the backend's
    default model for the manifest's tier, else the first model in that backend's
    catalogue (or None when it is empty)."""
    if backend == inference.DEFAULT_BACKEND and manifest.model_endpoint:
        return manifest.model_endpoint
    tiered = inference.default_key_for_profile(manifest.model_profile, backend)
    if tiered:
        return tiered
    entries = inference.entries(backend)
    return entries[0]["key"] if entries else None


def _judge_manifest(scenario: ScenarioConfig) -> AgentManifest | None:
    """The scenario's judge agent (first ``role == "judge"`` in the cast), or None."""
    registry = default_registry()
    for agent_name in scenario.cast:
        manifest = registry.agents.get(agent_name)
        if manifest is not None and manifest.role == "judge":
            return manifest
    return None


def _cast_defaults(scenario: ScenarioConfig, backend: str = inference.DEFAULT_BACKEND) -> dict[str, str]:
    """Default model selection for a scenario's *non-judge* cast (name → qualified key).

    The Judge is bound under §04, so it is excluded here.  Used to seed (and re-seed on
    scenario *or backend* change) the ``cast_models`` state the picker writes into."""
    registry = default_registry()
    defaults: dict[str, str] = {}
    for agent_name in scenario.cast:
        manifest = registry.agents.get(agent_name)
        if manifest is None or manifest.role == "judge":
            continue
        key = _default_model_key(manifest, backend)
        if key:
            defaults[agent_name] = key
    return defaults


def _voice_choices() -> list[tuple[str, str]]:
    """Narrator dropdown choices: (friendly label, voice id) for the four voices."""
    return [(f"{name} · {desc}", voice_id) for voice_id, (name, desc) in VOICES.items()]


# ── the component tree ───────────────────────────────────────────────────────────


def build_lab() -> dict[str, gr.components.Component]:
    """Build the Lab composer's Gradio tree and return its handles.

    Called inside the caller's ``with gr.Blocks(): gr.Tab("The Lab"):`` block.
    Wires no callbacks and imports no sibling render/show module — the app shell
    (Unit 9) binds ``summon_btn`` to the session.  Returns a dict of every handle a
    caller needs to read the composed run (keys: ``inference_backend, scenario, premise,
    seed, world, narrator, cast_models, judge_policy, judge_model, judge_strictness,
    tools, tokens, max_rounds, seed_num, cadence, summon_btn, surprise_btn``).
    ``inference_backend`` is the backend radio (``"modal"`` / ``"hf"``); ``cast_models``
    is a ``gr.State`` holding ``{agent_name: qualified_catalogue_key}`` for the non-judge
    cast (keys carry the backend, e.g. ``"hf:Qwen/Qwen2.5-7B-Instruct"``).
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

    # 00 — Inference backend: where the minds run.  This is the headline choice — it
    # decides which catalogue every model picker below draws from (Modal's self-hosted
    # vLLM endpoints, or Hugging Face's serverless Inference Providers).  Switching it
    # re-seeds the cast/judge picks to the new backend's models.
    with gr.Group():
        gr.Markdown("**00 · Inference backend** — where the minds think")
        handles["inference_backend"] = gr.Radio(
            choices=backend_choices(),
            value=inference.DEFAULT_BACKEND,
            label="Backend",
            info="Modal = vLLM you host · Hugging Face = serverless, many small models.",
        )
    backend_radio = handles["inference_backend"]

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

    # 03 — The Cast: one Modal-hosted model per mind.  The picker offers ONLY models in
    # the catalogue (modal/catalogue.py), and the choice drives the run (ADR-0022).  A
    # gr.render keeps one row per player as the scenario (and its cast size) changes; each
    # row's dropdown writes the chosen endpoint key into the cast_models state below.
    cast_models = gr.State(_cast_defaults(first))
    handles["cast_models"] = cast_models
    catalogue = model_choices()
    with gr.Group():
        gr.Markdown(
            "**03 · The Cast** — bind each mind to a model from the chosen backend "
            "(§00); the Judge is set in §04"
        )

        @gr.render(inputs=[handles["scenario"], backend_radio])
        def _render_cast(scenario_value, backend_value):
            scenario = _resolve_scenario(scenario_value)
            if scenario is None:
                gr.Markdown("_No scenario selected._")
                return
            backend_value = backend_value or inference.DEFAULT_BACKEND
            choices = model_choices(backend_value)
            backend_label = inference.backend_label(backend_value)
            registry = default_registry()
            shown = 0
            for agent_name in scenario.cast:
                manifest = registry.agents.get(agent_name)
                if manifest is None or manifest.role == "judge":
                    continue  # the Judge is configured under §04
                shown += 1
                with gr.Row():
                    gr.Markdown(
                        f"**{manifest.name}**<br/>"
                        f"<span style='opacity:.7'>{manifest.archetype or f'the {manifest.role}'}</span>"
                    )
                    picker = gr.Dropdown(
                        choices=choices,
                        value=_default_model_key(manifest, backend_value),
                        label=f"model · {backend_label}",
                        interactive=bool(choices),
                        scale=2,
                    )

                # Capture the agent name per row; the dropdown writes its key into the
                # shared cast_models dict the Summon handler reads.
                def _set_model(key, state, _name=manifest.name):
                    return {**(state or {}), _name: key}

                picker.change(_set_model, inputs=[picker, cast_models], outputs=[cast_models])
            if not shown:
                gr.Markdown("_This scenario has no selectable players._")
            elif not choices:
                gr.Markdown(f"_No {backend_label} models in the catalogue — the cast runs the deterministic stub._")

    # Switching scenarios *or backend* re-seeds the model picks to the new cast's defaults
    # so a stale override (from the previous world, or the other backend) never leaks in.
    def _reset_cast_models(scenario_value, backend_value):
        scn = _resolve_scenario(scenario_value)
        return _cast_defaults(scn, backend_value or inference.DEFAULT_BACKEND) if scn else {}

    handles["scenario"].change(
        _reset_cast_models, inputs=[handles["scenario"], backend_radio], outputs=[cast_models]
    )
    backend_radio.change(_reset_cast_models, inputs=[handles["scenario"], backend_radio], outputs=[cast_models])

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
                choices=catalogue,
                value=_default_model_key(_judge_manifest(first)) if _judge_manifest(first) else None,
                label="Judge model",
                interactive=bool(catalogue),
            )

            # The Judge picker repopulates from the chosen backend's catalogue whenever
            # the scenario (→ a different judge) or the backend changes, so it never
            # offers a model the selected backend can't run.
            def _reseed_judge(scenario_value, backend_value):
                scn = _resolve_scenario(scenario_value)
                backend_value = backend_value or inference.DEFAULT_BACKEND
                judge = _judge_manifest(scn) if scn else None
                choices = model_choices(backend_value)
                return gr.update(
                    choices=choices,
                    value=_default_model_key(judge, backend_value) if judge else None,
                    interactive=bool(choices),
                )

            handles["scenario"].change(
                _reseed_judge, inputs=[handles["scenario"], backend_radio], outputs=[handles["judge_model"]]
            )
            backend_radio.change(
                _reseed_judge, inputs=[handles["scenario"], backend_radio], outputs=[handles["judge_model"]]
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
    cast_models: dict[str, str] | None,
    judge_policy: str,
    judge_model: str,
    judge_strictness: float,
    tools: list[str],
    tokens: float | int | None,
    max_rounds: float | int | None,
    backend: str = inference.DEFAULT_BACKEND,
):
    """Assemble + validate a per-run world from the Lab's form values.

    Returns the validated :class:`WorldConfig` (raising ``pydantic.ValidationError``
    on an incoherent run).  This is the bridge the app shell uses to build a Conductor
    from a composed run via :meth:`Registry.from_world`.

    The base scenario (selected by its display *title* or internal name) supplies the
    cast roster and agent manifests.  ``backend`` (``"modal"`` / ``"hf"``) selects the
    inference backend; model selection binds each mind to a *specific* model on it:
    ``cast_models`` maps ``{agent_name: qualified_catalogue_key}`` for the players and
    ``judge_model`` is the Judge's key (§04).  Each becomes that agent's ``model_endpoint``
    (ADR-0022 / ADR-0024) — non-destructively via ``model_copy``, so the shared registry
    is untouched.  Only keys that exist in the unified ``inference`` registry are honoured
    (the picker offers nothing else; we re-check so a stale key — including one from the
    other backend after a switch — can't reach the run); an agent with no/blank/unknown
    selection keeps its manifest tier.  The premise overrides the scenario goal, ``seed``
    becomes its ``default_seed``, and the budget knobs feed the governor.

    The judge knobs (``judge_policy`` / ``judge_strictness``) and the ``tools`` grant are
    accepted and shape-checked, but the deeper synthesis (policy preset → judge behaviour,
    tool grants onto worker manifests) is deferred and TODO'd below — every dict this builds
    still passes through ``validate_scenario`` / ``validate_world`` before return, so the
    contract ("emit, validate, run") holds.  See ADR-0011 / ADR-0022.

    TODO: map ``judge_policy`` / ``judge_strictness`` to concrete judge behaviour and wire
    the selected ``tools`` onto each worker's manifest ``tools`` grant once those contracts
    land in the live path.
    """
    registry = default_registry()
    base = _resolve_scenario(scenario)  # accepts either display title or internal name
    if base is None:
        raise ValueError(f"unknown scenario {scenario!r} (have: {sorted(registry.scenarios)})")

    # Only catalogue-hosted models may be cast: the picker offers nothing else, and we
    # re-check each key against the unified backend registry so an out-of-band or stale
    # key (including one from the *other* backend after a switch) can never reach the run.
    def _valid(key: str) -> bool:
        return bool(key) and inference.entry_by_key(key) is not None

    selections = dict(cast_models or {})
    judge_key = (judge_model or "").strip()

    # Build the per-run cast: every manifest the scenario references, with its chosen
    # Modal model pinned via model_endpoint.  Non-destructive: model_copy, never mutate
    # the cached registry instance.
    agents = []
    cast_names: list[str] = []
    for agent_name in base.cast:
        manifest = registry.agents.get(agent_name)
        if manifest is None:
            raise ValueError(f"scenario {base.name!r} references undefined agent {agent_name!r}")
        # The Judge's model comes from §04; every other mind from the cast picker.
        chosen = judge_key if manifest.role == "judge" else selections.get(agent_name)
        patch: dict = {}
        if _valid(chosen):
            patch["model_endpoint"] = chosen
        manifest = manifest.model_copy(update=patch)
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

    world_dict: dict = {
        "agents": agents,
        "scenarios": [scenario_dict],
    }
    if governor:
        world_dict["governor"] = governor

    # The chosen backend decides this run's live/offline path: when it has credentials,
    # force the live path so the cast actually calls that backend's models (the per-agent
    # ``model_endpoint`` keys already carry the backend, so the router binds correctly).
    # With no credentials we leave it auto → the deterministic stub, so the offline demo
    # stays reproducible no matter which backend is selected.
    if inference.backend_available(backend):
        world_dict["models"] = {"offline": False}

    return validate_world(world_dict)


# ── e2e harness (not committed as a standalone entrypoint) ───────────────────────

if __name__ == "__main__":
    with gr.Blocks(title="Fishbowl · The Lab") as demo:
        with gr.Tab("The Lab"):
            build_lab()
    demo.launch()
