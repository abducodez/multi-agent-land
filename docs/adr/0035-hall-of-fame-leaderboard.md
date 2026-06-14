# ADR-0035: Hall of Fame — Dedicated Scoreboard Table, Detached from the Event Ledger

**Status:** Accepted
**Date:** 2026-06-14

## Context

Every competitive run now crowns a winner ([ADR-0029](0029-structured-verdicts.md),
[ADR-0030](0030-arena-scenarios-and-offline-winners.md)). What was missing was a
place to *remember*. Without a leaderboard the question "which model wins Debate Duel
the most?" has no answer — wins happen, are faithfully recorded on the ledger, and
then vanish from view the moment the next run starts.

The original design (the initial version of this ADR) proposed the leaderboard as a
*pure projection over the event stream* — a read-only fold with no new storage. That
approach was replaced, at the operator's explicit request, with the dedicated-table
design described here. The core trade-off: pure projections are elegant and
philosophically clean, but at page-load time they must fold the *entire* events log
— every utterance, thought, and verdict in every run — just to count wins. As the
ledger grows, that becomes an O(all-events) query on every Hall of Fame tab refresh.
A dedicated scoreboard table turns that into a cheap `SELECT` over a small,
already-decided set of rows.

Four constraints shaped the final design:

1. **One row per decided run, written once, at finish.** The scoreboard is not a live
   aggregate; it is a durable receipt. Writing at finalization time, under an explicit
   eligibility gate, keeps the table small and its rows trustworthy.
2. **Detached from the event ledger, linked by `run_id`.** The `events` table stays
   the source of truth for *what happened* in a run (the full trace — every utterance,
   thought, verdict, and poke). The `leaderboard_entries` table is a separate,
   denormalised record of *who won*. They share a database instance (the same
   `DATABASE_URL` / Postgres instance) but never share rows. The `run_id` foreign key
   on every leaderboard entry links back to the full trace for replay — so the
   separation is clean without being lossy.
3. **An explicit write gate.** A row is recorded only when the run is FINISHED, has a
   WINNER, carries at least one concrete WINNING MODEL endpoint, and is COMPETITIVE
   (`competition.kind != "none"`). This gate is the single choke point that keeps
   the scoreboard honest: abandoned runs, budget closures with no verdict, and
   exhibition-mode (`kind: none`) sessions never produce a row.
4. **Fairness is first-class.** Raw win counts mislead when seats are not symmetric.
   The `LeaderboardEntry` row carries the full competition shape (`kind`, `teams`,
   `symmetric_seats`), so the fairness rollup in `leaderboard.py` needs no registry
   lookup and no event replay.

Workstream 6 of the [arena roadmap](../architecture/next-steps/arena-roadmap.md)
specified the "Hall of Fame" UI tab. This ADR records what shipped.

## Decision

### 1. `src/core/leaderboard_store.py` — the dedicated, durable table

`LeaderboardEntry` (Pydantic v2) is the denormalised scoreboard row — one per decided
competitive run. It carries:

- `run_id` — links back to `ledger.events_for_run(run_id)` for replay (ADR-0027).
- `scenario`, `seed`, `cast` (cast→model bindings from the `run.started` stamp,
  ADR-0030) — everything needed to describe the run without touching the events log.
- `winner`, `winner_kind`, `winning_model`, `winning_models` — the structured verdict
  fields from ADR-0029.
- `competition_kind`, `teams`, `symmetric_seats` — the competition shape from
  ADR-0030, needed for per-seat fairness rollups.
- `reason`, `turns`, `tokens`, `started_at`, `finished_at`, `recorded_at`.

`LeaderboardStore` (SQLAlchemy, mirroring `SqlAlchemyLedger`'s lazy-import pattern)
owns the `leaderboard_entries` table. Its `record()` method is an idempotent upsert
keyed on `run_id` — delete-then-insert in one transaction, so a verdict that
supersedes a budget close replaces the row rather than duplicating it. `entries()` and
`entries_for_scenario()` are the two read paths.

`make_leaderboard_store()` is a memoised factory that resolves the same `DATABASE_URL`
as `ledger_factory.make_ledger`. No extra configuration is needed: the store is a
separate table in the same Postgres instance. The in-memory (`sqlite://`) stage demo
works end-to-end because the factory memoises by URL — the write at `finalize` and the
Hall of Fame read share the same in-memory engine.

`build_entry(summary, competition)` is the eligibility gate. It accepts a `RunSummary`
(folded from the run's events by `index_runs`) and the scenario's `CompetitionConfig`.
It returns `None` — producing no row — unless all four conditions are met: `finished_at`
is set, `winner` is non-empty, at least one winning model endpoint is non-empty, and
`competition.kind != "none"`.

### 2. The write path — `FishbowlSession._record_leaderboard()`

The write happens exactly once per run, inside `FishbowlSession.finalize()`, called
when the run reaches `"verdict"` or `"budget"`:

```python
def _record_leaderboard(self) -> None:
    # Folds the run's events into a RunSummary, checks eligibility via build_entry,
    # and writes one row to leaderboard_entries.  Idempotent.  Fully defensive —
    # any failure is swallowed so a store hiccup never breaks the show.
    run_events = self.conductor.ledger.events_for_run(self.conductor.run_id)
    summary = next((s for s in index_runs(run_events) if s.run_id == ...), None)
    entry = build_entry(summary, getattr(scenario, "competition", None))
    if entry is not None:
        store.record(entry)
```

The method is fully defensive: any store failure is logged and swallowed. A
leaderboard write hiccup never breaks a live run.

### 3. `src/core/leaderboard.py` — aggregations over `LeaderboardEntry` rows

The five public aggregation functions take `Sequence[LeaderboardEntry]` (not an event
stream). They never touch the `events` table.

| Function | Returns | Sort order |
|---|---|---|
| `scenario_sessions(entries, scenario_name)` | `list[LeaderboardEntry]` | newest-first by `finished_at`, `run_id` tiebreak |
| `model_table(entries)` | `list[ModelRow]` | `(-win_rate, -wins, -plays, model)` |
| `agent_table(entries, scenario_name)` | `list[AgentRow]` | `(-win_rate, -wins, -plays, agent)` |
| `fairness_table(entries, scenario_name)` | `list[SeatRow]` | `(-win_rate, -wins, -plays, seat_type)` |
| `headline(entries)` | `str \| None` | single summary sentence |

`headline` considers only symmetric-seat scenarios, requires ≥2 distinct models that
have each won ≥1 game, picks the scenario with the most decided games, and shortens
model endpoint slugs to the segment after the last `/`. It returns `None` when the
table holds no qualifying data — safe at app start.

Attribution rules are identical to the previous pure-projection design: a model earns
one play per run (endpoint set deduplicated); win credit goes to every endpoint in
`winning_models ∪ {winning_model}`. `fairness_table` counts only declared seats
(team members and `symmetric_seats` members); cast members with no seat type (judges,
narrators) are excluded so they never appear as zero-percent rows.

### 4. "Hall of Fame" Gradio tab in `src/ui/fishbowl/hall_of_fame.py`

The tab calls `make_leaderboard_store().entries()` on render, passes the resulting
`list[LeaderboardEntry]` to the five aggregation functions, and renders HTML. It never
touches the event ledger or the live `Conductor`. The per-row **Replay** button calls
`load_replay(run_id)` from `src/ui/fishbowl/archive.py` (ADR-0027) — the full event
trace is still in the ledger, accessible via `run_id`, so replay requires no change to
the transport mechanism.

## Consequences

### Positive

- **Cheap reads.** Hall of Fame renders are a `SELECT * FROM leaderboard_entries`,
  not an O(all-events) fold of the event log. The table stays small by design: only
  finished, won, competitive runs produce a row.
- **Clean separation of trace and scoreboard.** The `events` table is the authoritative
  record of what every agent said and thought. The `leaderboard_entries` table is the
  permanent record of who won. Neither needs the other to do its job.
- **Rebuildable.** The scoreboard is derived from events: every `LeaderboardEntry` can
  be reconstructed by replaying `events_for_run(run_id)` through `index_runs` and
  `build_entry`. The table is not a competing source of truth; it is a materialised
  view that happens to live in a dedicated table. If the table is lost or corrupted, a
  rebuild script using ADR-0026's `index_runs` can regenerate it.
- **Idempotent write.** The upsert-on-`run_id` means a corrective re-finalize (a
  budget close later superseded by a verdict — the ADR-0026 "last-wins" pattern)
  produces exactly one, current row. No duplicates; no gaps.
- **Fairness is first-class.** Per-seat win rates are built into the row schema, not
  bolted on later. The competition shape travels with the result, so no registry lookup
  is needed at render time.
- **Replay falls out for free.** `run_id` links every scoreboard row back to its full
  trace. ADR-0027's `load_replay` / `ReplaySession` already knows how to feed
  historical events into The Show; the Hall of Fame is just a new call site.
- **Deterministic tiebreaks.** Every sort ends on a string column (`model`, `agent`,
  `seat_type`, `run_id`) so table order is reproducible across re-renders.

### Negative / trade-offs

- **A second table can drift from the ledger.** The events log and the scoreboard are
  separate tables. If a run's events are manually edited or a schema migration changes
  the `run.finished` payload, the scoreboard row for that run will not update
  automatically. The `run_id` link and the idempotent upsert mitigate this: a
  re-finalize call regenerates the row from the current event state, and a rebuild
  script can do the same in bulk. But the drift risk is real and was not present in a
  pure-projection design.
- **Write-time eligibility is a point-in-time decision.** `build_entry` evaluates
  eligibility at `finalize` time, against the competition config that was loaded into
  the registry at that moment. A scenario YAML change that retroactively alters
  `competition.kind` will not affect already-written rows. This is generally the
  correct behaviour (the row reflects the game that was actually played), but operators
  should be aware of it.
- **SQLAlchemy is now required on the write path.** The store's lazy-import pattern
  (`SQLAlchemy` imported inside `__init__`) keeps the offline/stub path import-clean,
  but any production `finalize` call on a Postgres deployment requires the driver.
  This was already true of the `SqlAlchemyLedger` (ADR-0014); this ADR extends that
  dependency to the write side of the Hall of Fame.

### Neutral

- `headline` returns `None` at startup (no qualifying data). The UI renders a
  cheerful empty state; nothing is fabricated.
- Sort order is stable but opinionated: win-rate before raw wins, then plays, then
  name. A future "sort by column" affordance can override this without changing the
  aggregations.
- The store is co-located with the ledger (same `DATABASE_URL`) but is architecturally
  independent: it could be moved to a separate database by changing the URL passed to
  `make_leaderboard_store()`.

## Alternatives considered

- **Pure ledger projection (the original design).** The first version of this ADR
  described five pure functions over `Iterable[Event]` — no new table, no new storage,
  a perfect fit for ADR-0001's "ledger as source of truth" axiom. This was the
  correct starting point. It was set aside because the O(all-events) cost per render
  becomes perceptible as the ledger grows, and because the operator's explicit
  requirement was a dedicated, durable scoreboard row rather than a live computation.
  The pure-Python functions in `leaderboard.py` survive as aggregations over the
  dedicated table's rows, so the computation model is preserved — only the input
  changed from raw events to pre-decided `LeaderboardEntry` rows.

- **Extend `RunSummary` to carry the competition block.** This would have let a
  pure-projection leaderboard reuse `index_runs` output cleanly (the original design's
  main awkwardness). It was deferred rather than rejected: the change is mechanical but
  touches ADR-0026's public surface. With the dedicated-table design, the competition
  shape travels on the `LeaderboardEntry` row itself, making this less urgent.

- **SQL aggregate query as the primary path.** Faster at scale than folding Python
  objects, but adds a backend-specific code path and a `SqlAlchemyLedger`-only
  dependency into the aggregation module. The dedicated table achieves the same
  read-performance goal with a simpler interface (fold a small list of Pydantic
  objects). Revisit if the leaderboard's aggregation logic becomes a bottleneck.

- **Elo instead of win rate.** More statistically meaningful once session counts
  grow, but requires at least ~30 decided games per model pair to be trustworthy.
  Raw win rate is honest at small N and does not mislead when shown alongside the
  play count. Elo is noted in the arena roadmap's "Beyond" section.

## References

- [ADR-0001](0001-event-ledger.md) — event ledger as the single source of truth for
  *the trace*. The scoreboard is a derived, rebuildable materialisation keyed by
  `run_id`, not a competing source of truth.
- [ADR-0014](0014-postgres-event-store.md) — durable Postgres store behind the ledger
  interface; the `DATABASE_URL` the leaderboard store shares.
- [ADR-0026](0026-run-lifecycle-and-history.md) — run lifecycle; `run.started` /
  `run.finished`; `RunSummary`; `index_runs` — the fold `build_entry` calls.
- [ADR-0027](0027-per-user-sessions-and-archive-replay.md) — `ReplaySession` and
  `load_replay`; the replay transport the Hall of Fame Replay button reuses via `run_id`.
- [ADR-0029](0029-structured-verdicts.md) — structured `winner` / `winner_kind` /
  `winning_models` on `run.finished`; the source of the scoreboard's attribution fields.
- [ADR-0030](0030-arena-scenarios-and-offline-winners.md) — `competition` block
  stamped on `run.started`; `symmetric_seats`; the eight arena-grade scenarios. The
  competition shape travels onto every `LeaderboardEntry` row.
- `src/core/leaderboard_store.py` — `LeaderboardEntry`, `LeaderboardStore`,
  `build_entry`, `make_leaderboard_store`.
- `src/core/leaderboard.py` — the five aggregation functions over `LeaderboardEntry`
  rows.
- `src/ui/fishbowl/session.py` — `FishbowlSession._record_leaderboard()`, the write
  path.
- `src/ui/fishbowl/hall_of_fame.py` — the Gradio tab.
- [arena roadmap § Workstream 6](../architecture/next-steps/arena-roadmap.md) — the
  original specification these projections realise.
