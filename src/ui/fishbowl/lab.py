"""Fishbowl · The Lab — the Gradio composer that turns knobs into a runnable world.

This is the left half of the two-tab theater: a form that adapts to the selected
scenario.  Its sections — Scenario & Goal, The Initiator, The Cast, (optionally) The
Judge — and the per-agent cards are derived from the *effective* cast, so a world with
no judge draws no Judge section and a tool checkbox only ever appears for a mind that
may actually call a tool (``scenario_caps``).

Two surfaces live here, kept deliberately separate:

  * :func:`build_lab` builds the Gradio component *tree* inside a caller's ``gr.Blocks``
    and returns a ``dict`` of handles.  It wires *no* cross-tab callbacks — the app shell
    (Unit 9) owns the Summon button and the session.  This module never imports sibling
    show modules, so the composer stays independent of the live stage.

  * :func:`collect_world_config` is a *pure* helper that assembles a per-run, WorldConfig
    -shaped dict from the form values and validates it with ``validate_world`` /
    ``validate_scenario`` (``src/core/config``).  It never mutates registry state — it
    reads the registry's manifests and rebuilds a fresh, self-contained world (per-agent
    edits applied non-destructively via ``model_copy``).  See ADR-0011 / ADR-0022 / ADR-0025.
"""

from __future__ import annotations

import html

import gradio as gr

from src.core.config import GovernorConfig, ScenarioConfig, validate_scenario, validate_world
from src.core.manifest import AgentManifest, ScheduleConfig
from src.core.registry import default_registry
from src.models import inference
from src.tools.builtins import default_tool_registry
from src.ui.fishbowl.adapter import VOICES, scenario_voice
from src.ui.fishbowl.render.agent_panel import render_agent_panel
from src.ui.fishbowl.render.scenario_panel import render_scenario_panel
from src.ui.fishbowl.scenario_caps import scenario_ui_caps

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
# ui/raw/lab.jsx:MCP_TOOLS; the value is what we store on the run.  Only ids the
# engine actually has (see ``available_tool_ids``) are ever surfaced as a grant.
TOOL_CHOICES: list[tuple[str, str]] = [
    ("dice.roll · randomness source", "dice.roll"),
    ("vote.tally · count the room", "vote.tally"),
    ("lore.append · write to canon", "lore.append"),
    ("mood.read · sense the table", "mood.read"),
    ("oracle · ask the unseen", "oracle"),
    ("tts.speak · give it a voice", "tts.speak"),
]

# Friendly labels for the tool ids, for the per-agent tool picker.
_TOOL_LABELS: dict[str, str] = {tool_id: label for label, tool_id in TOOL_CHOICES}

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


def available_tool_ids() -> set[str]:
    """Tool ids the live tool registry can actually dispatch.

    The Lab only offers grants the engine can honour: an in-process registration or,
    when the MCP transport is configured, a tool the resolver advertises.  Any friendly
    ``TOOL_CHOICES`` entry the engine lacks is filtered out so a granted tool always
    resolves (no dead checkboxes, no capability violation at runtime).
    """
    registry = default_tool_registry()
    ids: set[str] = set()
    for _label, tool_id in TOOL_CHOICES:
        if registry.has(tool_id):
            ids.add(tool_id)
    return ids


def _tool_choices_for(manifest: AgentManifest, available: set[str]) -> list[tuple[str, str]]:
    """The (label, id) tool grants to offer *manifest*, or [] for a non-tool agent.

    Only a *tool-capable* mind — one whose manifest already grants a tool — gets a tool
    picker, so a checkbox never appears on an agent that was never meant to use tools (the
    event/capability contract decides who is tool-capable, not the UI).  Within that, we
    offer the agent's granted ids intersected with what the live registry can dispatch, so
    the user may keep or drop a grant but never add a dead/ungranted capability.
    """
    if not manifest.tools:
        return []
    offer = [t for t in manifest.tools if t in available]
    return [(_TOOL_LABELS.get(tool_id, tool_id), tool_id) for tool_id in sorted(offer)]


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


_DIRECTOR_MODE = "⚙ Director's cut"
_QUICK_MODE = "✦ Quick"


def _world_summary_html(scenario: ScenarioConfig | str, roster: list[str] | None = None) -> str:
    """A glanceable digest of the chosen world: its goal + a row of capability badges.

    Reads the *effective* cast (``scenario_caps``) so the digest tells the truth even after
    a roster edit — how many minds wake, whether one of them judges, what tools are in play,
    and the turn budget.  This is the 'understand the world before you touch a knob' surface
    that makes the Lab digestible; it carries no state (pure HTML), so it is safe to re-emit
    on every scenario/roster change.
    """
    scn = _resolve_scenario(scenario) if isinstance(scenario, str) else scenario
    if scn is None:
        return "<div class='lab-ws'><div class='lab-ws-goal'>No world selected.</div></div>"
    caps = scenario_ui_caps(scn, cast_override=roster)
    count = len(caps.cast)
    badges = [f"<span class='lab-badge'>{count} mind{'' if count == 1 else 's'}</span>"]
    if caps.judge is not None:
        badges.append(f"<span class='lab-badge badge-judge'>⚖ {html.escape(caps.judge.name)} judges</span>")
    else:
        badges.append("<span class='lab-badge badge-muted'>no judge · open-ended</span>")
    if caps.has_tools:
        tool_ids = sorted({t for grants in caps.tool_agents.values() for t in grants})
        badges.append(f"<span class='lab-badge badge-tool'>🛠 {html.escape(', '.join(tool_ids))}</span>")
    else:
        badges.append("<span class='lab-badge badge-muted'>no tools</span>")
    gov = scn.governor
    if gov is not None and gov.max_turns:
        badges.append(f"<span class='lab-badge'>≤ {gov.max_turns} turns</span>")
    return (
        "<div class='lab-ws'>"
        f"<div class='lab-ws-title'>{html.escape(scn.title or scn.name)}</div>"
        f"<div class='lab-ws-goal'>{html.escape(scn.goal or '')}</div>"
        f"<div class='lab-ws-badges'>{''.join(badges)}</div>"
        "</div>"
    )


# ── component tree ───────────────────────────────────────────────────────────────


def build_lab() -> dict[str, gr.components.Component]:
    """Build the Lab composer's Gradio tree and return its handles.

    Called inside the caller's ``with gr.Blocks(): gr.Tab("The Lab"):`` block.  Wires no
    cross-tab callbacks and imports no show module — the app shell (Unit 9) binds
    ``summon_btn`` to the session.  The Cast + Judge sections live inside a ``gr.render``
    keyed on the scenario, backend, *and* cast roster, so they adapt as the user edits the
    roster: each non-judge mind gets an editable card (``agent_panel``), the Judge section
    appears only when the effective cast has a judge, and a tool picker appears only on a
    tool-capable mind.  Per-agent edits land in four ``gr.State`` dicts keyed by agent name
    (``cast_models``, ``cast_tools``, ``cast_personas``, ``cast_schedules``).

    Returns a dict of every handle a caller needs to read the composed run.
    """
    scenarios = _ordered_scenarios()
    first = scenarios[0]
    titles = [s.title or s.name for s in scenarios]
    caps0 = scenario_ui_caps(first)

    handles: dict[str, gr.components.Component] = {}

    gr.Markdown(
        "### The Lab · compose the experiment\n"
        "Pick a world and press **Summon** — that's the whole story. Want to direct it? "
        "Flip to **Director's cut** to retune every mind."
    )

    # ── Quick lane — always visible.  Three taps to a show: pick a world, read its
    # digest, (optionally) choose the opening beat, Summon.  Everything heavier lives
    # under Director's cut, so a newcomer is never asked to parse a wall of knobs.
    with gr.Group():
        gr.Markdown("**Pick a world** — the cast and controls adapt to whatever you choose")
        handles["scenario"] = gr.Radio(
            choices=titles,
            value=titles[0],
            label="Scenario",
            elem_classes=["lab-scenario-pick"],
            info="Each world wakes a different cast of small minds.",
        )

    # A live digest of the chosen world — goal + capability badges — so you understand it
    # at a glance before touching anything.  Reseeded on scenario/roster change below.
    world_summary = gr.HTML(_world_summary_html(first), elem_classes=["lab-ws-wrap"])

    # The one heavier knob surfaced up front: the opening beat.  A dropdown of the scenario's
    # example seeds picks a starting beat and drops its text into the (hidden) editable box —
    # which is the value Summon actually reads.  The box stays out of the way until the small
    # "edit" button reveals it, so the Quick lane reads as one clean picker.  (Premise, genesis,
    # roster and budget live under Director's cut.)  The app shell reseeds the box's value on
    # scenario change; the preset list is reseeded just below.
    with gr.Row():
        seed_presets = gr.Dropdown(
            choices=first.example_seeds or [first.default_seed],
            value=first.default_seed,
            label="Seed — pick an opening beat",
            filterable=False,
            info="Choose a starting beat, or hit edit to write your own.",
            scale=8,
        )
        seed_edit_btn = gr.Button("✎ edit", size="sm", scale=0, elem_classes=["lab-seed-edit"])
    handles["seed"] = gr.Textbox(
        value=first.default_seed,
        label="…the beat the conductor writes (edit freely)",
        lines=2,
        visible=False,
    )
    seed_presets.change(lambda beat: beat, inputs=[seed_presets], outputs=[handles["seed"]])

    # The edit button toggles the textbox; its label flips so the control reads as a switch.
    seed_edit_open = gr.State(False)

    def _toggle_seed_edit(is_open):
        now_open = not is_open
        return now_open, gr.update(visible=now_open), gr.update(value="✓ done" if now_open else "✎ edit")

    seed_edit_btn.click(
        _toggle_seed_edit,
        inputs=[seed_edit_open],
        outputs=[seed_edit_open, handles["seed"], seed_edit_btn],
    )

    # Mode switch — progressive disclosure.  Quick shows only the essentials above; the
    # Director's cut reveals backend, scenario detail, the cast, and the judge.
    mode = gr.Radio(
        choices=[_QUICK_MODE, _DIRECTOR_MODE],
        value=_QUICK_MODE,
        show_label=False,
        elem_classes=["lab-mode"],
    )

    # ── Director's cut — hidden until asked for.  Holds every advanced knob; toggling
    # ``mode`` flips this column's visibility.  ``backend_radio`` is bound here (it decides
    # which catalogue the cast/judge pickers draw from), so the cast render below sees it.
    with gr.Column(visible=False, elem_classes=["lab-advanced"]) as advanced:
        with gr.Group():
            gr.Markdown("**Backend** — where the minds think")
            handles["inference_backend"] = gr.Radio(
                choices=backend_choices(),
                value=inference.DEFAULT_BACKEND,
                label="Backend",
                info="Modal = vLLM you host · Hugging Face = serverless, many small models.",
            )
        backend_radio = handles["inference_backend"]

        # Scenario detail — goal, genesis, cast roster, and the governor budget.
        panel = render_scenario_panel(first, available_agents=caps0.available_agents)
        handles["premise"] = panel.premise
        handles["world"] = panel.world
        handles["cast_roster"] = panel.cast_roster
        handles["max_turns"] = panel.max_turns
        handles["max_calls_per_turn"] = panel.max_calls_per_turn
        handles["max_total_tokens"] = panel.max_total_tokens
        handles["hourly_budget_usd"] = panel.hourly_budget_usd
        cast_roster = panel.cast_roster

        # Per-agent edit state — one dict per editable field, keyed by agent name.  Seeded
        # from the lead scenario; re-seeded on scenario/backend change (and the cast render
        # rewrites them as the user edits).  cast_schedules holds {agent: {tick_every, ...}}.
        cast_models = gr.State(_cast_defaults(first))
        cast_tools = gr.State({})
        cast_personas = gr.State({})
        cast_schedules = gr.State({})
        handles["cast_models"] = cast_models
        handles["cast_tools"] = cast_tools
        handles["cast_personas"] = cast_personas
        handles["cast_schedules"] = cast_schedules

        available_tools = available_tool_ids()
        catalogue0 = model_choices()

        # The Cast: one *collapsed* editable accordion per non-judge mind, derived from the
        # *effective* roster so the form adapts as the user edits it.  The card count varies,
        # so this is a ``gr.render``; each card writes its edits into the per-field State
        # dicts (the stable handles the Summon handler reads), as the model picker always has.
        with gr.Group():
            gr.Markdown(
                "**The Cast** — expand a mind to bind its model, grant a tool, rewrite its persona, or retime it"
            )

            @gr.render(inputs=[handles["scenario"], backend_radio, cast_roster])
            def _render_cast(scenario_value, backend_value, roster_value):
                scenario = _resolve_scenario(scenario_value)
                if scenario is None:
                    gr.Markdown("_No scenario selected._")
                    return
                backend_value = backend_value or inference.DEFAULT_BACKEND
                caps = scenario_ui_caps(scenario, cast_override=roster_value)
                choices = model_choices(backend_value)
                backend_label = inference.backend_label(backend_value)

                workers = caps.worker_cast
                if not workers:
                    gr.Markdown("_This scenario has no selectable players._")
                for index, manifest in enumerate(workers):
                    tool_choices = _tool_choices_for(manifest, available_tools)
                    card = render_agent_panel(
                        manifest,
                        model_choices=choices,
                        model_value=_default_model_key(manifest, backend_value),
                        backend_label=backend_label,
                        tool_choices=tool_choices,
                        start_open=(index == 0),  # open the lead mind so the section isn't opaque
                    )
                    _wire_agent_card(
                        card,
                        cast_models=cast_models,
                        cast_tools=cast_tools,
                        cast_personas=cast_personas,
                        cast_schedules=cast_schedules,
                    )

                if workers and not choices:
                    gr.Markdown(
                        f"_No {backend_label} models in the catalogue — the cast runs the deterministic stub._"
                    )

        # The Judge.  Static handles (the app shell reads them on Summon and the picker
        # offers the catalogue), wrapped in a Group whose visibility tracks the effective
        # roster: a judge-less cast hides the whole section so its knobs never apply.  The
        # legacy global ``tools`` handle is retained as a hidden State (live grants flow
        # per-agent through ``cast_tools``).
        handles["tools"] = gr.State([])
        judge0 = caps0.judge
        with gr.Group(visible=caps0.has_judge, elem_classes=["lab-judge-card"]) as judge_group:
            gr.Markdown("**The Judge** — the mind that records the verdict")
            handles["judge_policy"] = gr.Dropdown(
                choices=JUDGE_POLICIES,
                value=JUDGE_POLICIES[0],
                label="Policy preset",
            )
            handles["judge_model"] = gr.Dropdown(
                choices=catalogue0,
                value=_default_model_key(judge0) if judge0 else None,
                label="Judge model",
                interactive=bool(catalogue0),
                filterable=False,  # pick from the catalogue; no free-text filtering
            )
            handles["judge_strictness"] = gr.Slider(
                minimum=0,
                maximum=100,
                value=50,
                step=1,
                label="Strictness (lenient → merciless)",
            )

    # Flip the whole Director's-cut column on the mode switch.
    mode.change(lambda m: gr.update(visible=(m == _DIRECTOR_MODE)), inputs=[mode], outputs=[advanced])

    # Keep the world digest honest: refresh it when the scenario changes (read the new
    # world's own cast) and when the roster is edited (read the effective cast).
    handles["scenario"].change(
        lambda scenario_value: _world_summary_html(_resolve_scenario(scenario_value)),
        inputs=[handles["scenario"]],
        outputs=[world_summary],
    )
    cast_roster.change(
        lambda scenario_value, roster_value: _world_summary_html(_resolve_scenario(scenario_value), roster_value),
        inputs=[handles["scenario"], cast_roster],
        outputs=[world_summary],
    )

    # Refresh the seed preset list to the new world's example beats (the editable seed box's
    # value is reseeded by the app shell).
    def _reseed_seed_presets(scenario_value):
        scn = _resolve_scenario(scenario_value)
        if scn is None:
            return gr.update()
        return gr.update(choices=list(scn.example_seeds) or [scn.default_seed], value=scn.default_seed)

    handles["scenario"].change(_reseed_seed_presets, inputs=[handles["scenario"]], outputs=[seed_presets])

    # The Judge section's visibility + its model picker re-seed from the *effective* cast
    # (so dropping the judge hides it) and the chosen backend (so it never offers a model
    # the backend can't run).  *override* is the roster when the edit was a roster change;
    # on a scenario/backend change the roster has just been reset, so we derive from the
    # scenario's own cast (the just-set roster value isn't visible to this handler yet).
    def _judge_update(scn, backend_value, override):
        backend_value = backend_value or inference.DEFAULT_BACKEND
        if scn is None:
            return gr.update(visible=False), gr.update()
        caps = scenario_ui_caps(scn, cast_override=override)
        choices = model_choices(backend_value)
        judge = caps.judge
        return (
            gr.update(visible=caps.has_judge),
            gr.update(
                choices=choices,
                value=_default_model_key(judge, backend_value) if judge else None,
                interactive=bool(choices),
            ),
        )

    def _reseed_judge_scenario(scenario_value, backend_value):
        return _judge_update(_resolve_scenario(scenario_value), backend_value, None)

    def _reseed_judge_roster(scenario_value, backend_value, roster_value):
        return _judge_update(_resolve_scenario(scenario_value), backend_value, roster_value)

    _judge_outputs = [judge_group, handles["judge_model"]]
    handles["scenario"].change(
        _reseed_judge_scenario, inputs=[handles["scenario"], backend_radio], outputs=_judge_outputs
    )
    backend_radio.change(_reseed_judge_scenario, inputs=[handles["scenario"], backend_radio], outputs=_judge_outputs)
    cast_roster.change(
        _reseed_judge_roster, inputs=[handles["scenario"], backend_radio, cast_roster], outputs=_judge_outputs
    )

    # Switching scenario *or backend* re-seeds the per-agent states, the roster, and the
    # governor knobs so a stale override (from the previous world or the other backend)
    # never leaks in.  The roster reset re-fires the judge/cast renders downstream.
    def _reset_cast(scenario_value, backend_value):
        scn = _resolve_scenario(scenario_value)
        backend_value = backend_value or inference.DEFAULT_BACKEND
        if scn is None:
            return ({}, {}, {}, {}, gr.update(), gr.update(), gr.update(), gr.update(), gr.update())
        caps = scenario_ui_caps(scn)
        gov = scn.governor or GovernorConfig()
        return (
            _cast_defaults(scn, backend_value),
            {},  # cast_tools (cards seed from each manifest's grant)
            {},  # cast_personas (blank → keep written persona)
            {},  # cast_schedules
            gr.update(value=list(caps.cast_names)),
            gr.update(value=gov.max_turns),
            gr.update(value=gov.max_calls_per_turn),
            gr.update(value=gov.max_total_tokens),
            gr.update(value=gov.hourly_budget_usd),
        )

    _reset_outputs = [
        cast_models,
        cast_tools,
        cast_personas,
        cast_schedules,
        cast_roster,
        panel.max_turns,
        panel.max_calls_per_turn,
        panel.max_total_tokens,
        panel.hourly_budget_usd,
    ]
    handles["scenario"].change(_reset_cast, inputs=[handles["scenario"], backend_radio], outputs=_reset_outputs)
    backend_radio.change(_reset_cast, inputs=[handles["scenario"], backend_radio], outputs=_reset_outputs)

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


def _wire_agent_card(
    card,
    *,
    cast_models: gr.State,
    cast_tools: gr.State,
    cast_personas: gr.State,
    cast_schedules: gr.State,
) -> None:
    """Wire one rendered agent card's controls into the shared per-field State dicts."""
    name = card.name

    def _set_model(key, state, _name=name):
        return {**(state or {}), _name: key}

    card.model.change(_set_model, inputs=[card.model, cast_models], outputs=[cast_models])

    if card.tools is not None:

        def _set_tools(grants, state, _name=name):
            return {**(state or {}), _name: list(grants or [])}

        card.tools.change(_set_tools, inputs=[card.tools, cast_tools], outputs=[cast_tools])

    if card.persona is not None:

        def _set_persona(text, state, _name=name):
            return {**(state or {}), _name: text or ""}

        card.persona.change(_set_persona, inputs=[card.persona, cast_personas], outputs=[cast_personas])

    if card.tick_every is not None and card.max_consecutive is not None:

        def _set_schedule(tick, consec, state, _name=name):
            entry: dict = {}
            if tick is not None and tick != "":
                entry["tick_every"] = int(tick)
            if consec is not None and consec != "":
                entry["max_consecutive"] = int(consec)
            return {**(state or {}), _name: entry}

        card.tick_every.change(
            _set_schedule, inputs=[card.tick_every, card.max_consecutive, cast_schedules], outputs=[cast_schedules]
        )
        card.max_consecutive.change(
            _set_schedule, inputs=[card.tick_every, card.max_consecutive, cast_schedules], outputs=[cast_schedules]
        )


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
    cast_tools: dict[str, list[str]] | None = None,
    cast_personas: dict[str, str] | None = None,
    cast_schedules: dict[str, dict] | None = None,
    cast_roster: list[str] | None = None,
    genesis: str | None = None,
    max_turns: float | int | None = None,
    max_calls_per_turn: float | int | None = None,
    max_total_tokens: float | int | None = None,
    hourly_budget_usd: float | int | None = None,
):
    """Assemble + validate a per-run world from the Lab's form values.

    Returns the validated :class:`WorldConfig` (raising ``pydantic.ValidationError`` on an
    incoherent run).  This is the bridge the app shell uses to build a Conductor from a
    composed run via :meth:`Registry.from_world`.

    The base scenario (selected by its display *title* or internal name) supplies the
    cast roster and agent manifests.  ``cast_roster`` (when given) overrides which agents
    wake up — built from the registry, validated against it.  ``backend`` selects the
    inference backend; ``cast_models`` maps ``{agent_name: qualified_catalogue_key}`` for
    the players and ``judge_model`` is the Judge's key (§04).

    Per-agent edits are applied **non-destructively** via ``model_copy`` so the shared
    registry is never mutated:

      * ``model_endpoint`` ← the chosen catalogue key (only keys the unified ``inference``
        registry knows are honoured — a stale/unknown/cross-backend key is dropped);
      * ``tools`` ← ``cast_tools[name]`` (validated against the live tool registry — an
        ungranted/unknown id is filtered out so the run can never reference a dead tool);
      * ``persona`` ← a non-blank ``cast_personas[name]`` override;
      * ``schedule`` ← ``cast_schedules[name]`` merged onto the manifest's schedule.

    Scenario fields: the premise overrides the goal, ``seed`` becomes ``default_seed``,
    ``genesis`` (when given) overrides ``genesis_text``, and the governor knobs
    (``max_turns`` / ``max_calls_per_turn`` / ``max_total_tokens`` / ``hourly_budget_usd``,
    with legacy ``max_rounds`` / ``tokens`` honoured as fallbacks) feed the governor.

    The judge knobs (``judge_policy`` / ``judge_strictness``) are accepted and shape-checked;
    mapping a policy preset to concrete judge *behaviour* remains a presentation-only label
    for now.  Every dict this builds passes through ``validate_scenario`` / ``validate_world``
    before return, so the "emit, validate, run" contract holds.  See ADR-0011 / ADR-0022 / ADR-0025.
    """
    registry = default_registry()
    base = _resolve_scenario(scenario)  # accepts either display title or internal name
    if base is None:
        raise ValueError(f"unknown scenario {scenario!r} (have: {sorted(registry.scenarios)})")

    # Only catalogue-hosted models may be cast: the picker offers nothing else, and we
    # re-check each key against the unified backend registry so an out-of-band or stale
    # key (including one from the *other* backend after a switch) can never reach the run.
    def _valid_model(key: str) -> bool:
        return bool(key) and inference.entry_by_key(key) is not None

    selections = dict(cast_models or {})
    tool_edits = dict(cast_tools or {})
    persona_edits = dict(cast_personas or {})
    schedule_edits = dict(cast_schedules or {})
    judge_key = (judge_model or "").strip()

    # Only tools the live registry can actually dispatch may be granted — a UI grant for a
    # tool the engine lacks is filtered out so no run references a dead capability.
    valid_tools = available_tool_ids()

    # Effective roster: the user's override (built from the registry) or the scenario's cast.
    roster = [name for name in (cast_roster or base.cast) if name]
    # Drop blanks/dupes while preserving order.
    seen: set[str] = set()
    ordered_roster: list[str] = []
    for name in roster:
        if name not in seen:
            seen.add(name)
            ordered_roster.append(name)
    roster = ordered_roster or list(base.cast)

    # Build the per-run cast: every manifest the roster references, with its UI edits
    # pinned via model_copy.  Non-destructive: never mutate the cached registry instance.
    agents = []
    cast_names: list[str] = []
    for agent_name in roster:
        manifest = registry.agents.get(agent_name)
        if manifest is None:
            raise ValueError(f"scenario {base.name!r} references undefined agent {agent_name!r}")
        patch: dict = {}

        # Model: the Judge's comes from §04; every other mind from the cast picker.
        chosen = judge_key if manifest.role == "judge" else selections.get(agent_name)
        if _valid_model(chosen):
            patch["model_endpoint"] = chosen

        # Tool grant: the UI only ever offers an agent its *own* manifest grant intersected
        # with what the live registry can dispatch, so we re-check both here — a stale or
        # crafted edit can keep/drop a real grant but never escalate a non-tool mind into a
        # capability it was never given (matching ``_tool_choices_for`` and the docstring).
        if agent_name in tool_edits:
            allowed = valid_tools & set(manifest.tools)
            granted = [t for t in (tool_edits.get(agent_name) or []) if t in allowed]
            patch["tools"] = granted

        # Persona: a non-blank override replaces the written identity.
        persona = (persona_edits.get(agent_name) or "").strip()
        if persona:
            patch["persona"] = persona

        # Schedule: merge any edited knobs onto the manifest's existing schedule.
        sched = schedule_edits.get(agent_name)
        if isinstance(sched, dict) and sched:
            merged = manifest.schedule.model_dump()
            for field_name in ("tick_every", "max_consecutive"):
                if field_name in sched and sched[field_name] is not None:
                    merged[field_name] = sched[field_name]
            patch["schedule"] = ScheduleConfig(**merged)

        manifest = manifest.model_copy(update=patch)
        agents.append(manifest.model_dump(mode="python"))
        cast_names.append(manifest.name)

    # Per-run scenario: premise overrides goal, chosen seed becomes the default, an
    # explicit genesis override replaces the scenario's own pre-loaded world state.
    scenario_dict = {
        "name": base.name,
        "title": base.title,
        "goal": (premise or "").strip() or base.goal,
        "default_seed": (seed or "").strip() or base.default_seed,
        "example_seeds": list(base.example_seeds),
        "cast": cast_names,
        "genesis_text": (genesis.strip() if isinstance(genesis, str) and genesis.strip() else base.genesis_text),
    }

    # Governor budget from the run knobs (None/blank → omit so defaults apply).  The new
    # governor params win; legacy ``max_rounds`` / ``tokens`` are honoured as fallbacks.
    governor: dict = {}
    turns = max_turns if max_turns else max_rounds
    if turns:
        governor["max_turns"] = int(turns)
    if max_calls_per_turn:
        governor["max_calls_per_turn"] = int(max_calls_per_turn)
    total_tokens = max_total_tokens if max_total_tokens else tokens
    if total_tokens:
        governor["max_total_tokens"] = int(total_tokens)
    if hourly_budget_usd:
        governor["hourly_budget_usd"] = float(hourly_budget_usd)
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
