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
run.started · world.observed · agent.thought · agent.spoke
agent.reflected · judge.verdict · user.injected
```

Any other well-formed kind (e.g. `oracle.spoke`, `crier.announced`) is valid and,
if it carries a `text` payload, renders on stage via the generic projection
fallback.

## Evolution Rules

- Add fields instead of renaming fields; keep history immutable.
- New kinds are additive by construction (the schema is open).
- Validate all events before append (Pydantic, `extra="forbid"`).
- Bump `schema_version` and add upcasters before reading old event versions.
