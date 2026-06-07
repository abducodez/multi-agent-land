# Architecture Overview

## Shape

One event-sourced engine; every world is a *configuration* of it.  Agents on the
left produce into a shared ledger; oversight and rendering on the right consume
from it.  The two sides never call each other.

```text
                 config/ (YAML)  ─────────────────────────────┐
                 agents · scenarios · models                  │ Registry
                                                               ▼
Visitor ─► Gradio UI ─► Conductor ─► ManifestAgent ─► ModelRouter ─► small model
                          │  ▲           │  └─► ToolRegistry (capability-checked)
                  Governor│  │           ▼
              (calls/tokens) │      Event Ledger (append-only, idempotent)
                             │           │
                             │      Projections ──► Observer ──► Gradio UI
                             └───────────┘
```

The ledger is the only source of truth.  Agent memory, world state, UI state, and
bloggable traces are all projections derived from events.

## The four stable contracts

Keep these stable and everything else is hot-swappable (config over code):

1. **Event schema** — `src/core/events.py` (open, namespaced kinds; ADR-0009)
2. **Ledger API** — `src/core/ledger.py` / `sqlite_ledger.py` (interface, not impl)
3. **Agent manifest** — `src/core/manifest.py` (declarative agent contract)
4. **Tool contract** — `src/tools/registry.py` (capability-checked; ADR-0012)

## The layers that make it modular

| Concern | Mechanism | Doc |
|---|---|---|
| What agents/scenarios exist, who participates | declarative `config/` + `Registry` | [config-system.md](config-system.md) · [scenario-authoring.md](scenario-authoring.md) |
| Which (small) model each agent uses | `ModelRouter`, per-agent profile | [model-routing.md](model-routing.md) |
| How agents talk | append-only ledger + subscription/tick routing | [subscription-routing.md](subscription-routing.md) |
| What agents remember | episodic / salience / reflection (ledger views) | [memory-stack.md](memory-stack.md) |
| What agents can do | capability-checked tools | [tool-contract.md](tool-contract.md) |
| Running for hours | two-clock conductor, ledger checkpoint, token governor | [long-running.md](long-running.md) |
| Rendering | read-only observer + projections | [observer-pattern.md](observer-pattern.md) |

## Configurable from a UI or an LLM

Because configuration is validatable data (`WorldConfig` / `AgentManifest` /
`ScenarioConfig`), the same surface a human edits can be emitted by a UI form or
proposed by an agent and checked with one `validate_world()` call before it runs.

## Design Pressure

Favour delight and demo reliability over platform completeness.  Architecture
exists to make the toy more surprising, observable, and extensible — and to prove,
with `tests/test_modularity.py`, that a new world is a file, not a fork.
