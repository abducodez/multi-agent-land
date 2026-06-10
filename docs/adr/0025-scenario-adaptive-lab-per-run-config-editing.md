# ADR-0025: Scenario-Adaptive Lab + Per-Run Config Editing

## Status

Accepted

## Context

ADR-0021 gave us the Fishbowl Lab (a Gradio composer) and ADR-0022 made the per-agent
**model** picker load-bearing (`model_endpoint` flows through `collect_world_config` →
`Registry.from_world` → `Conductor`). Two gaps remained, both visible the moment you
switch worlds:

1. **The form was not valid per scenario.** The §04 Judge section and a global §05 Tools
   checkbox rendered for *every* scenario — including the two with no judge (Open Table,
   Oracle Grove) and the four with no tool-using agent. The form lied about the run: it
   offered knobs the selected world cannot honour.
2. **Agent/scenario config was barely editable and partly unwired.** The only per-agent
   control was the model dropdown. `judge_policy` / `judge_strictness` / `tools` were
   collected and then dropped on the floor (an explicit `TODO` in `collect_world_config`).
   A user could not change a persona, grant/revoke a tool, retune the schedule, edit the
   genesis text, change the roster, or set the governor budget — yet all of these are real
   `AgentManifest` / `ScenarioConfig` fields the engine consumes at runtime.

The hackathon bar is "AI is load-bearing" and "delightful": the cast's *behaviour* is the
product, so the form that shapes that behaviour should expose it — and only the parts that
are real for the chosen world.

## Decision

Make the Lab a **lens over the effective cast**, and let every edit reach the run
**per-run and non-destructively** — the same proven path ADR-0022 used for `model_endpoint`
(`manifest.model_copy(update=...)` + a freshly-assembled `scenario_dict`). The shared
`default_registry()` and `config/*.yaml` are never mutated; the offline demo stays
reproducible.

- **Capability lens.** A new pure module `src/ui/fishbowl/scenario_caps.py` derives, from
  the *effective* cast (the scenario's roster, or the user's roster override), a small typed
  `ScenarioCaps`: the judge manifest (or `None`), the worker cast, and which agents may use
  which tools. The Lab draws the Judge section only when `caps.has_judge`, and a tool
  picker only on a tool-capable mind.
- **Per-agent cards.** `src/ui/fishbowl/render/agent_panel.py` renders one editable card
  per non-judge mind: model (as before) + a per-agent tool `CheckboxGroup` (only for
  tool-capable agents) + a persona override + schedule knobs (`tick_every` /
  `max_consecutive`). The event contract (`subscribes_to` / `may_emit`) rides along as
  **read-only** info chips — it is fragile and stays untouched.
- **Scenario panel.** `src/ui/fishbowl/render/scenario_panel.py` renders the scenario
  knobs: goal, seed, genesis text, a **cast-roster multiselect** over the registry, and the
  governor bounds (`max_turns` / `max_calls_per_turn` / `max_total_tokens` /
  `hourly_budget_usd`).
- **State + reseeding.** Per-agent edits accumulate in four `gr.State` dicts keyed by agent
  name (`cast_models`, `cast_tools`, `cast_personas`, `cast_schedules`). Switching scenario
  or backend re-seeds the states, the roster, and the governor to the new world's defaults;
  the Cast/Judge `gr.render` is keyed on `[scenario, backend, cast_roster]` so it adapts as
  the roster is edited (dropping the judge hides the Judge section live).
- **Progressive disclosure.** The breadth above is a lot to hand a newcomer at once, so the
  Lab presents two speeds. A **Quick** lane (default) shows only the essentials — the
  scenario gallery, a live **world digest** (goal + capability badges: cast size, judge or
  not, tools, turn budget — derived from `scenario_caps`), the opening seed, and Summon. A
  **Director's cut** toggle reveals the rest (backend, goal/genesis, roster, governor, and
  the cast) in one hidden column, and each mind is a *collapsed* accordion (title =
  `name · archetype · tier · 🛠`) you expand only to tune. The mode switch and digest are
  internal presentation (not part of the `build_lab` handle contract).
- **Assembly.** `collect_world_config` applies each edit via `model_copy`: `model_endpoint`
  (catalogue-checked, as before), `tools` (**double-checked** against both the live tool
  registry *and* the agent's own manifest grant — a stale/crafted edit can keep or drop a
  real grant but never escalate a non-tool mind into a capability it was never given),
  non-blank `persona`, merged `schedule`. The roster overrides the `cast` list; genesis and
  the governor knobs feed `scenario_dict`. Everything still passes `validate_scenario` /
  `validate_world`.

## Consequences

- The form tells the truth: each world shows only the controls it can honour (judge present
  in Wood / Mystery Roots / The Steeped; hidden in Open Table / Oracle Grove; the `oracle`
  tool checkbox only on Oracle Grove's `fortune-teller`).
- The cast's behaviour is now directly composable from the UI — persona, tools, schedule,
  roster, goal, seed, genesis, budget — and the edits drive the live run, not a cosmetic
  copy. This sharpens the "AI is load-bearing" story.
- All edits are per-run and non-destructive: `config/*.yaml` and the cached registry are
  untouched, so the deterministic offline demo is byte-reproducible and a "bad" edit can
  never corrupt a canonical world. A compose/validate error still degrades to the default
  cast (ADR-0022), so Summon never breaks.
- The tool grant is capability-safe: the UI only offers an agent its own manifest grant ∩
  what the engine can dispatch, and `collect_world_config` re-checks both, so no run can
  reference a dead or un-granted tool.
- Persistence is explicitly out of scope: the Lab does not write YAML. Durable edits would
  need a separate registry write-back path (deferred).

## Alternatives considered

- **Write edits back to `config/*.yaml`.** Durable across sessions, but mutates checked-in
  config, needs registry cache invalidation + validation-on-write, and risks breaking a
  world for everyone. Rejected in favour of the safe, reproducible per-run override.
- **Keep one static form for all scenarios.** Simplest, but keeps the form lying about
  judge/tools per world — the exact problem this ADR fixes.
- **Expose `subscribes_to` / `may_emit` for editing.** These define the event contract and
  turn order; a careless edit yields an incoherent run. Kept read-only.

## Code

- `src/ui/fishbowl/scenario_caps.py` — `scenario_ui_caps`, `ScenarioCaps`
- `src/ui/fishbowl/render/agent_panel.py` — `render_agent_panel`
- `src/ui/fishbowl/render/scenario_panel.py` — `render_scenario_panel`
- `src/ui/fishbowl/lab.py` — `build_lab` (adaptive render + reseeding), `collect_world_config`
  (per-agent/per-scenario patches), `available_tool_ids`, `_tool_choices_for`
- `src/ui/fishbowl/app.py` — `_compose_session`, the `Summon` input wiring

See also: ADR-0011 (declarative validatable config), ADR-0021 (Fishbowl Gradio presenter),
ADR-0022 (per-agent explicit model binding).
