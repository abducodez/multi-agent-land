# Scenario Config Contract

A scenario is declarative data: a goal, a seed, and a **cast of agent names**.
Defined by `ScenarioConfig` (`src/core/config.py`), loaded from
`config/scenarios/<name>.yaml`, validated by `validate_scenario()`.

## Schema

```yaml
name: mystery-roots            # unique slug (required)
title: "🔍 Mystery Roots"      # display name for the UI (optional)
goal: >                        # shared objective, injected into every prompt
  Converge on the most interesting, evidence-supported explanation.
default_seed: "All the clocks stopped at 3:07."   # required
example_seeds:                 # gallery seeds for the UI
  - "All the clocks stopped at 3:07."
  - "The bridge appeared overnight."
cast:                          # agent names, resolved via the agent registry
  - clue-gatherer
  - hypothesis-former
  - devils-advocate
  - mystery-judge
genesis_text: "A mystery settles over the wood: {seed}"   # '{seed}' substituted
governor:                      # optional per-scenario budget (else defaults)
  max_turns: 2000
  max_calls_per_turn: 16
  max_total_calls: 20000
```

## Fields

| Field | Meaning |
|---|---|
| `name` | Unique slug; the registry key. |
| `title` | UI display label; falls back to `name`. |
| `goal` | The shared objective.  Rendered as a `SHARED GOAL` block in every agent prompt and carried on the genesis `run.started` event (`projection.goal`).  This is how a scenario "sets up the goal." |
| `default_seed` | Seed used when none is supplied. |
| `example_seeds` | Seed gallery for the UI dropdown. |
| `cast` | Agent names that participate.  **Selecting who participates is editing this list.**  Each must exist in `config/agents/`. |
| `genesis_text` | Template for the opening `world.observed`; `{seed}` is replaced. |
| `governor` | Optional `GovernorConfig`; omit for engine defaults. |

## Scheduling lives on the agents

A scenario does **not** declare a scheduling policy.  Cadence is per-agent —
each cast member's manifest carries `subscribes_to` (reactive) and
`schedule.tick_every` (periodic).  The conductor routes accordingly.  (The legacy
`Scenario.schedule()` method remains only as the Phase-0/1 fallback for agents
without a manifest.)

## Building one

```python
from src.core.registry import default_registry
scenario = default_registry().build_scenario("mystery-roots")   # cast -> live agents
```

See also: [agent-manifest.md](agent-manifest.md), [world-config.md](world-config.md).
