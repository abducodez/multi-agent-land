# ADR-0026: Run Lifecycle, Provenance, and Multi-Run History in One Ledger

## Status

Accepted

## Context

The engine has always known how a run *begins* — `Conductor.reset()` mints a `run_id`
and appends `run.started` — but it never recorded how a run *ends*, and `reset()` itself
was destructive in a way that fights the persistent store. Three gaps:

1. **No terminal event.** A run stopped for many reasons — the judge reached a
   `judge.verdict`, the governor tripped (`BudgetExceeded`, ADR-0013), the tick cap was
   hit, or the operator pressed stop — but the ledger held no record of *which*, nor a
   tidy summary (who won, on what model, how many turns/tokens). Every consumer (UI,
   trace export, resume) had to re-derive "is this run over and why" from the tail.
2. **`reset()` wiped the database.** `reset()` called `self.ledger.reset()`, which on a
   persistent backend (`make_ledger()` always returns `SqlAlchemyLedger`; the Fishbowl
   session already uses it) erased *every* prior run. Starting a second show silently
   destroyed the first — directly at odds with wanting a durable, shareable history.
3. **Thin provenance + run-blind projections.** `run.started` carried only `{seed, goal}`,
   so a replay could not tell which scenario ran or which model each agent was bound to
   (ADR-0022's per-agent binding was invisible after the fact). And projections/memory
   rebuilt from *all* ledger events, so they could not be scoped to a single run once the
   DB held many.

The hackathon bar wants the append-only ledger to *be* the shareable agent trace
(Sharing-is-Caring badge). A trace you can't bound to one run, that loses its own start
provenance, and that gets clobbered on the next Summon is not shareable.

## Decision

Treat the ledger as a **multi-run history**: one durable store holds many runs, each
framed by an enriched `run.started` and a new `run.finished`, queryable per run. All
changes are additive — `schema_version` stays **1** (ADR-0009 open/additive kinds), no
migration.

- **New terminal kind `run.finished`** (added to `CORE_EVENT_KINDS`, regex-valid under
  ADR-0009). Payload shape:

  ```
  {
    "reason": "verdict" | "budget" | "tick_cap" | "user_stop",
    "winner": str | None,          # actor name of the winner, if any
    "winning_model": str | None,   # model_endpoint bound to the winner, if known
    "turns": int,                  # turns elapsed in the run
    "tokens": int                  # total tokens spent in the run
  }
  ```

  `winner`/`winning_model` are populated from a `judge.verdict` when the reason is
  `verdict` (the winner's model is resolved through the cast→model map below); both are
  `null` for budget/tick-cap/user-stop endings. `turns`/`tokens` are read from the
  governor's live stats (`src/core/governor.py`).

- **Enriched `run.started` payload.** Keep the existing `{seed, goal}` and add, alongside
  them, the **scenario name** and a **cast→model binding map** (ADR-0022 made visible
  *after* the fact):

  ```
  {
    "seed": str, "goal": str,                       # existing — unchanged
    "scenario": str,                                # scenario name
    "cast": {                                       # one entry per agent
       "<agent_name>": {"model_endpoint": str | None,
                        "model_profile": "tiny"|"fast"|"balanced"|"strong"}
    }
  }
  ```

  The map is built from `self.scenario.agents`, reading each `agent.manifest.model_endpoint`
  / `model_profile` and guarding agents that lack a manifest (`getattr(agent,"manifest",None)`).
  Purely additive keys, so old readers ignore them and `schema_version` stays 1.

- **Non-destructive `reset()`.** On a persistent ledger, `reset()` **no longer wipes the
  DB**. It mints a new `run_id`, clears only the conductor's *in-memory* turn state
  (queues, `agent_errors`, governor, turn counter), and appends a fresh enriched
  `run.started` plus the scenario genesis. Prior runs stay on disk, so one DB accumulates
  a history of shows. (The in-memory `Ledger` used by some tests is naturally fresh per
  instance; the destructive `ledger.reset()` call is dropped from the lifecycle.)

- **Run-scoped projections (optional `run_id`).** `rebuild_stage(events, run_id=None)` and
  `EpisodicMemory/SalienceMemory.visible(events, run_id=None)` gain an optional `run_id`
  that, when given, filters the tuple to that run before projecting — trivial because
  every `Event` carries `run_id`. Default `None` preserves today's whole-ledger behaviour,
  so existing callers and tests are untouched.

- **New ledger query API.** Add `events_for_run(run_id) -> tuple[Event, ...]` and
  `runs() -> list[...]` (one descriptor per `run.started`, newest first, with id + scenario
  + start time) to the ledger surface. Both SQL backends already index `run_id`, so
  `events_for_run` is an indexed query mirroring the existing `tail(from_offset)` /
  `latest_offset()` style; the in-memory backend filters its `events` tuple.

## Consequences

- **The trace is self-describing and bounded.** A single run.started→…→run.finished slice
  names its scenario, its full cast→model binding, how it ended, who won on what model, and
  its turn/token cost — ready to export to the HF Hub as a stand-alone trace.
- **History survives.** Summoning a new show no longer destroys prior runs; one DB is a
  gallery of shows. The UI can list `runs()` and replay any one via `events_for_run`.
- **Run-scoped views, no double counting.** Stage and memory can be rebuilt for a chosen
  run even when the store holds many, so a multi-run DB doesn't bleed one show's lines into
  another's blackboard.
- **Governor stops are recorded, not just raised.** `BudgetExceeded` (ADR-0013) still
  bubbles, but the lifecycle now also writes a `run.finished{reason:"budget"}` so the stop
  is visible in the trace rather than inferred from an absent next event.
- **Additive only.** No schema bump, no migration; old ledgers and old readers keep
  working, and the deterministic offline stub stays byte-reproducible.
- **Garbage collection is out of scope.** An ever-growing single DB will eventually want
  pruning/archival of old runs; deferred until the history is large enough to matter.

## Alternatives considered

- **Keep wiping on reset, one run per DB.** Simplest, but loses all history on Summon and
  makes a shareable multi-run trace impossible — the exact problem this ADR fixes.
- **Separate DB file per run.** Preserves isolation, but fragments the history, complicates
  `runs()`/replay, and breaks the "one append-only ledger is the trace" story. Rejected for
  a single run-scoped store.
- **Derive end-reason/winner at read time instead of a `run.finished` event.** Keeps the
  ledger thinner, but every consumer re-implements the heuristic and a budget/user stop
  leaves no positive marker. An explicit terminal event is the append-only-friendly record.
- **Bump `schema_version` for the richer payloads.** Unnecessary — all additions are new
  keys/kinds, which ADR-0009's open/additive contract already covers at version 1.

## References

- Builds on ADR-0009 (open/additive event kinds — no migration for new kinds/keys) and
  ADR-0022 (per-agent explicit model binding — the source of the cast→model map).
- Relates to ADR-0013 (token governor — supplies the budget stop reason and turn/token
  stats) and ADR-0024 (observability).
- `src/core/events.py` — `CORE_EVENT_KINDS` (`run.finished`), documented payload shapes
- `src/core/conductor.py` — non-destructive `reset()`, enriched `run.started`, `run.finished`
- `src/core/projections.py`, `src/core/memory.py` — optional `run_id` scoping
- `src/core/ledger.py`, `src/core/sqlalchemy_ledger.py` — `events_for_run`, `runs`
