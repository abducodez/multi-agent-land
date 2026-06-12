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
competition:                   # optional contest contract (ADR-0029); absent == none
  kind: judged                 # versus | judged | none
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
| `competition` | Optional `CompetitionConfig` — does this scenario produce a winner, and how?  See below. |
| `governor` | Optional `GovernorConfig`; omit for engine defaults. |

## Competition: who can win, and who decides

A scenario declares whether it produces a winner with the optional `competition:`
block (`CompetitionConfig`, ADR-0029).  Absent block == `kind: none` — full sessions
and history, but nobody wins.

```yaml
competition:
  kind: versus | judged | none   # default none
  teams:                         # versus only
    spy: [spy-nil]
    herd: [spy-cara, spy-bex, spy-ovo]
```

The three kinds split *who derives the winner*:

| kind | ground truth? | winner derived by | shipped example |
|---|---|---|---|
| `versus` | yes — the team map | **code** — the scenario's handler scores the judge's accusation against `teams` | `the-steeped` |
| `judged` | no — judgment *is* the result | **the model** — the judge's validated `winner` field | `mystery-roots` |
| `none` | n/a | nobody — judges don't declare `winner` at all | everything else |

Validation rules (enforced in `CompetitionConfig` and `WorldConfig`,
`src/core/config.py` — a bad block fails loudly at load, per ADR-0011):

- `teams` is permitted only when `kind: versus`, and is required (non-empty) there.
- Member lists must be non-empty and **mutually disjoint** — no double agents.
- Every team member must appear in the scenario's `cast`.
- **No team label may equal an agent name.**  The `winner` payload key carries either
  an agent name or a team label; this rule keeps that union unambiguous.

The registry injects the competition context into the cast's agents at build time
(the same seam as `agent.manifest`), which arms verdict validation in the base agent.
The machine-readable verdict and run-summary keys this produces are documented in
[events.md](events.md#verdict-and-run-payloads-adr-0029).

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
