# ADR-0013: Token-Aware Governor + Long-Running Foundations

## Status

Accepted

## Context

The Governor counted calls only, and the conductor had no way to resume a run or
pace itself across wall-clock time.  "Run for hours without falling over" needs
three things the engine lacked: a budget that understands tokens (the real cost
driver), a checkpoint to recover from, and a way to map a production cadence onto
simulation time.

## Decision

Extend, don't replace — all new limits default to off so existing behaviour is
unchanged.

**Governor** (`src/core/governor.py`): adds `max_total_tokens` and
`hourly_budget_usd` alongside the call caps; `record_call(tokens, cost_usd)` is
metered each turn; `reset()` zeroes counters while keeping configured limits
(replacing the old in-place `__init__` hack).  Providers expose `last_usage`; the
conductor records real token counts.

**Conductor** (`src/core/conductor.py`):
- `step(n_ticks=1)` advances N sim-ticks in one call — the two-clock foundation,
  so a wall-clock "one episode per hour" maps to `step(n_ticks=60)`.
- `restore()` resumes a persisted run by adopting the ledger's `run_id` and last
  `turn`; with `SQLiteLedger.from_file()` the ledger *is* the checkpoint.
- `snapshot_every` periodically checkpoints a SQLite-backed ledger.

## Consequences

- A many-small-models topology can't silently exhaust a token budget.
- A process kill + relaunch continues from the last committed event
  (`scripts/resume_run.py` demonstrates it; `tests/test_long_running.py` proves
  it).
- Wall-clock cadence and durable execution (cron, Temporal, Modal) layer on top
  without engine changes — they call `step(n_ticks=…)` and persist the ledger.
- Cost-per-token wiring (LLM observability) is a documented next step; tokens are
  already metered.
