# ADR-0011: Declarative, Validatable Configuration

## Status

Accepted

## Context

"Super modular" and "easily configurable" require that the configurable
surface — which agents exist, who participates, their model tier and memory, the
scenario goal, tool grants, budgets — be **data, not code**.  It must be editable
by hand, by a future UI form, or by an LLM, and the engine must be able to
*verify* a proposed configuration before running it.  Hardcoded Python casts
cannot satisfy any of that.

## Decision

Express every knob as a declarative document with a Pydantic schema:

- `AgentManifest` (the agent contract) — persona, role, subscriptions, `may_emit`,
  schedule, `model_profile`, memory, `tools`, optional `handler`.
- `ScenarioConfig` — `name`, `goal`, `default_seed`, `example_seeds`, `cast`
  (agent names), `genesis_text`, optional `governor`.
- `ModelsConfig` / `GovernorConfig` — model profiles and budgets.
- `WorldConfig` — a whole world inline (agents + scenarios + models + budgets),
  cross-validated so every scenario's cast references a defined agent.

These live as YAML under `config/` and are loaded by `Registry`
(`src/core/registry.py`), which resolves a scenario's `cast` into live agents.
`validate_world` / `validate_agent` / `validate_scenario` turn an arbitrary dict
into a typed, cross-checked object or a precise error — so "configure from a
prompt" reduces to *emit JSON → validate → run*.

Behaviour stays in Python only where needed: an agent names a `handler` and the
registry instantiates the registered subclass; most agents need none.

## Consequences

- Adding an agent or scenario, picking the cast, or wiring a tool is editing a
  YAML file — proven by `tests/test_modularity.py` (a brand-new agent + scenario
  runs with zero engine edits).
- The same schemas validate UI-form output and LLM-proposed configs, making a
  no-code or agent-built configuration path safe.
- A small `pyyaml` dependency is added.
- The shipped scenarios are now config (`config/scenarios/*.yaml`); their Python
  modules are thin `build_scenario()` shims that delegate to the registry.
