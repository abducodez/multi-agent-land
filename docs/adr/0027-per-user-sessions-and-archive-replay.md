# ADR-0027: Per-User Sessions and Read-Only Archive Replay

## Status

Accepted

## Context

ADR-0026 made the ledger a durable, multi-run history: many runs live in one store,
each framed by an enriched `run.started` and a `run.finished`, and the live Conductor
projection is run-scoped. That unlocked "keep the history of runs" at the data layer —
but the Fishbowl UI had two gaps that surfaced as soon as the store became shared:

1. **The live transcript bled across runs and scenarios.** `FishbowlSession.events`
   returned `conductor.ledger.events` — the *entire* shared ledger — and `snapshot()`
   fed that unscoped into `view_model_at` → `rebuild_stage`. With one durable store
   holding every run, the Show for scenario B would replay scenario A's discussion.
   This is the "show the right discussion for the right session/scenario" bug.
2. **No notion of "whose run".** Runs were not attributed to a user/browser, so there
   was no basis for "show me *my* past sessions" — and no UI to load them. Summon
   already started a fresh run (ADR-0026's non-destructive reset), but there was no way
   back to a previous one.

The product ask: Summon always starts a fresh conversation; previous sessions appear
only behind a **Load** affordance; each user/browser gets a unique session id; and the
Archive lists *my* sessions for *this* world only.

## Decision

Attribute runs to a per-browser session id, scope every Show read to a single run, and
add a read-only **Archive** that replays a past run without spending tokens. All
additive; `schema_version` stays **1** (ADR-0009).

- **`run.started.session_id` (optional).** `Conductor.reset(seed, *, session_id=None)`
  stamps the id onto `run.started` when present (key omitted otherwise). `RunSummary`
  (ADR-0026) carries `session_id` so the run-index folds it for free. No side table —
  the run list stays a projection of the log (ADR-0014).

- **The session id is the browser's, persisted in `localStorage`.** Resolved (or minted
  with `crypto.randomUUID`) by a small JS function on `demo.load`, written into a hidden
  carrier, and read by Python. It survives reloads, so "my sessions" is stable per user
  without server-side accounts.

- **Run-scoped live transcript.** `FishbowlSession.events` now returns
  `events_for_run(conductor.run_id)`. Every downstream read (`head`, `snapshot`, the
  scrubber, autoplay) flows from this, so scoping one property scopes the whole Show —
  scenario B can never show scenario A's discussion.

- **`ReplaySession` — a read-only view over one past run.** It exposes the exact read
  surface the Show renders (`events`/`head`/`snapshot`/`has_verdict`) over a fixed
  `events_for_run(run_id)` slice, rebuilding cast/meters from the run's own scenario
  (named on `run.started`). `replay = True`; `step`/`step_one`/`inject` are no-ops, and
  the autoplay loop replays the recorded prefix then stops — **a load never generates.**

- **`src/ui/fishbowl/archive.py`.** `list_runs(scenario, session_id)` folds the ledger
  via `index_runs_from_ledger` and keeps only this user's runs in this world, newest
  first; `load_replay(run_id)` builds the `ReplaySession`; `run_card_label` renders a
  one-line phosphor card.

- **Archive drawer in the Lab.** A `gr.Accordion` under Summon with a `gr.render` keyed
  on (scenario, session id) that lists clickable cards; a card loads the replay and
  jumps to the Show. The render re-runs on world change, when the id resolves from
  localStorage, and on manual refresh.

## Consequences

- The Show is correct under a shared, multi-user store: each user drives their own
  `run_id` in their own `gr.State`, sees only their own live run, and can browse only
  their own history. Multiple browsers naturally share the store while staying isolated.
- The Archive is "nearly free" on top of ADR-0026 — pure reads, no new persistence.
- `ReplaySession` cannot resume a past run (read-only by design); resuming would need a
  live Conductor rehydrated at that `run_id` (a future step if wanted).
- localStorage ties "my sessions" to a browser profile; clearing storage starts a new
  identity. Acceptable for a no-accounts demo; a future signed-in id could supersede it.
- Postgres is unchanged — no new tables — keeping the ledger the single source of truth
  (ADR-0014). A materialized run-summary table remains an option if listing ever needs
  to scale beyond folding the log.
