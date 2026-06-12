# ADR-0028: Session-Stamped Events and Run-Scoped Memory Recall

## Status

Accepted

## Context

ADR-0027 attributed *runs* to a browser session via `run.started.payload.session_id`,
and scoped the **UI** to one run. Three gaps remained on the data/context side:

1. **Actions were not attributable.** Only `run.started` carried the session id; the
   events an agent actually produced (`agent.spoke`, `judge.verdict`, …) did not, so
   "all actions by user X" required a join through `run.started` in every consumer
   (SQL, mem0, exports).
2. **Agent context bled across runs.** `Conductor._run_agent` passed the *whole*
   ledger (`ledger.events` — every run, every user) as `recent_events`, so episodic
   and salience memory could recall another show's — another user's — discussion
   into a prompt.
3. **Semantic recall was unscoped.** The mem0 index (ADR-0018/0019/0020) stored and
   searched one global namespace (`user_id="ledger"`), ignoring mem0's native
   `run_id`/`agent_id` identity scopes; cross-run hits crowded the relevance budget.

Additionally, the session id originates in `localStorage` — untrusted client input —
and reached the ledger unvalidated.

## Decision

- **`Event.session_id: str | None` on the envelope.** Stamped by the Conductor at
  the single `_append` chokepoint from the run's normalized session id; agents and
  scenarios never know sessions exist. Nullable (headless runs stay `None`).
  Persisted as an indexed nullable column in both SQL backends. Additive —
  `schema_version` stays 1. *No migration shipped:* we are pre-release; recreate dev
  DB files instead.
- **Normalize at the engine boundary.** `normalize_session_id()` (events.py) accepts
  `[A-Za-z0-9._-]{1,64}` and degrades anything else to `None` (logged) — applied in
  `Conductor.reset` (write path) and `archive.list_runs` (read path). A tampered
  localStorage can never break Summon or inject garbage into the store.
- **Run-scoped agent context.** `_run_agent` now passes
  `events_for_run(self.run_id)` as `recent_events`. One line; closes the prompt
  bleed for both memory layers, since every recall folds from that slice.
- **mem0 native scoping.** `MemoryIndex.search(query, k, run_id=None)`; backends
  store with mem0's native `run_id=event.run_id` / `agent_id=event.actor` and filter
  search by `run_id`, with a defensive post-filter on the reconstructed event.
  `SalienceMemory` derives the scope from its candidates (single-run slice → scoped
  search, free for callers). `session_id` rides in index metadata for forensics.
- **RunIndex prefers the envelope.** `RunSummary.session_id` folds from
  `event.session_id` first, payload copy second.

## Consequences

- Every action is directly filterable by session in SQL (`WHERE session_id = ?`,
  indexed), in mem0 metadata, and in exported traces (the JSONL dump inherits the
  envelope field for free).
- Prompts are hermetic per run: neither the episodic window, nor salience ranking,
  nor semantic recall can surface another run's text. Verified by probe-agent and
  scoped-search tests.
- Existing dev databases predate the `session_id` column and must be deleted (or
  recreated) — accepted in lieu of a migration while pre-release.
- mem0's `_indexed` dedup set is process-local; a persistent vector store may
  re-embed after restart (idempotent by `event.id`, so correct — just re-work).
