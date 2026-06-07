# Event Schema

## Envelope

| Field | Purpose |
| --- | --- |
| `id` | Unique event id for idempotency |
| `run_id` | Groups events into one run |
| `turn` | Logical turn counter |
| `kind` | Event taxonomy key |
| `actor` | Agent, scenario, visitor, or system that emitted the event |
| `payload` | Event-specific JSON data |
| `created_at` | UTC timestamp |
| `schema_version` | Additive schema evolution marker |

## Initial Event Kinds

- `run.started`
- `world.observed`
- `agent.thought`
- `agent.spoke`
- `judge.verdict`
- `user.injected`

## Evolution Rules

- Add fields instead of renaming fields.
- Keep event history immutable.
- Validate all events before append.
- Add upcasters before reading old event versions.

