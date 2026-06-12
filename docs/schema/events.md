# Event Schema

The event is the first stable contract.  Defined in `src/core/events.py`.

## Envelope

| Field | Purpose |
| --- | --- |
| `id` | Unique event id for idempotency |
| `run_id` | Groups events into one run |
| `turn` | Logical (sim-time) turn counter |
| `kind` | Namespaced event kind (see below) |
| `actor` | Agent, scenario, visitor, or system that emitted the event |
| `payload` | Event-specific JSON data |
| `created_at` | UTC timestamp |
| `schema_version` | Additive schema evolution marker |

## Kinds are open and format-validated (ADR-0009)

`kind` is **not** a closed enum.  It is any lowercase, dot-namespaced identifier:

```
^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+$      e.g. agent.spoke, clue.found, episode.published
```

Malformed kinds (no dot, uppercase, symbols) are rejected.  A scenario mints new
kinds with **zero engine edits** — that is the modularity contract for the schema.

- **Shape** is validated here (the regex).
- **Authority** — which agent may emit which kind — is enforced per turn by each
  agent's `manifest.may_emit`, not by the schema.

### Core kinds

`CORE_EVENT_KINDS` are the kinds the engine special-cases (projection rendering,
memory importance defaults).  It is a default set, not a gate:

```
run.started · run.finished · world.observed · agent.thought · agent.spoke
agent.reflected · judge.verdict · user.injected
```

Any other well-formed kind (e.g. `oracle.spoke`, `crier.announced`) is valid and,
if it carries a `text` payload, renders on stage via the generic projection
fallback.

## Verdict and run payloads (ADR-0029)

In a scenario with a [`competition:` block](scenario-config.md#competition-who-can-win-and-who-decides),
the winner is data, not just prose.  Two core kinds carry it — all keys additive,
`schema_version` stays 1 (ADR-0009).

### `judge.verdict`

Alongside the spoken `text` (and any manifest extras like `mood`):

| key | type | when present | meaning |
|---|---|---|---|
| `winner` | `str \| None` | `judged` and `versus` | agent name (*judged* — the model's validated pick) or team label (*versus* — stamped by code) |
| `accused` | `str` | `versus` | the judge's named pick, preserved before code overwrites `winner` — keeps the trace auditable |
| `correct` | `bool` | `versus` | ground truth: was the accused actually the spy? |
| `scores` | `dict[str, float]` | when in the judge's `output_extra_fields` | per-agent map, cleaned in code: unknown names dropped, values clamped to 0–10 |
| `no_contest` | `true` | on failure | the model named an invalid winner and one corrective re-ask didn't fix it; `winner` is dropped, the verdict `text` still ships |

An invalid `winner` (not a cast name, not a team label) triggers **one** re-ask in
`ManifestAgent` (`src/agents/base.py`), with both calls' token usage summed so the
governor meters the retry (ADR-0013).  A missing `winner` is never an error — the
offline stub doesn't emit it, so deterministic demos are unaffected.

### `run.finished`

The attribution contract (ADR-0026, extended by ADR-0029) — mirrored on `RunSummary`:

| key | type | meaning |
|---|---|---|
| `winner` | `str \| None` | display name for the leaderboard row — agent name or team label |
| `winner_kind` | `"agent" \| "team" \| None` | how to read `winner`: checked against the run's cast map first, then team labels |
| `winning_model` | `str \| None` | unchanged legacy key — a single agent winner's `model_endpoint`; `None` for team wins (never a guess) |
| `winning_models` | `list[str]` | the winner's endpoint, or every winning-team member's (`None` entries dropped) |

`FishbowlSession.finalize` (`src/ui/fishbowl/session.py`) resolves the kind and
models when stamping `run.finished`.

## Evolution Rules

- Add fields instead of renaming fields; keep history immutable.
- New kinds are additive by construction (the schema is open).
- Validate all events before append (Pydantic, `extra="forbid"`).
- Bump `schema_version` and add upcasters before reading old event versions.
