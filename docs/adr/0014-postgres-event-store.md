# ADR-0014: Durable Postgres Event Store Behind the Ledger Interface

## Status

Accepted

## Context

The append-only event ledger is the single source of truth: all projections,
memory, and crash recovery derive from it (ADR-0001). `SQLiteLedger` (ADR-0013,
`docs/architecture/persistence.md`) made that log durable on a local file, which
is enough for one process on one disk. A hosted, multi-instance deployment wants
a managed database: durable across restarts, reachable by several workers, and
operated by someone else. Managed Postgres — e.g. Neon — is the natural target,
but the engine must not be coupled to a single vendor or to a database being
present at all: the suite has to stay green with no connection.

Two product requests shaped the implementation: use **SQLAlchemy** for the store,
and use the **`eventsourcing`** library. We evaluated `eventsourcing`'s
persistence primitives against our model and they do not compose cleanly (see
below).

## Decision

Add a durable backend *for* the ledger; do not replace the ledger. The `Event`
envelope and the `Ledger` interface are unchanged.

**Layered.** `SqlAlchemyLedger(Ledger)` (`src/core/sqlalchemy_ledger.py`) is a
drop-in backend that mirrors `SQLiteLedger`'s surface — `append`, `events`,
`reset`, `extend`, plus `snapshot_to` / `from_file` / `tail` / `latest_offset` /
`close`. Idempotency is a `UNIQUE` constraint on the event `id`; insertion order
is a serial `offset` column (not `created_at`, which is subject to clock skew, nor
`turn`, which repeats on retry) — the same guarantees as the SQLite backend. The
*same* SQLAlchemy code drives both Postgres and SQLite, so the backend is
exercised in CI against SQLite without a server, and the Neon path is
code-identical.

**Env-gated, offline by default.** A small factory (`src/core/ledger_factory.py`,
`make_ledger()`) selects the backend: `DATABASE_URL` set → `SqlAlchemyLedger`;
unset → the in-memory `Ledger`. With no `DATABASE_URL` the system never imports
SQLAlchemy or a driver. SQLAlchemy is imported lazily inside the backend, so
importing `src.core.*` does not require the library to be installed. The store
deps (`sqlalchemy>=2.0`, `psycopg[binary]>=3` — the Neon driver) are **core
dependencies** in `pyproject.toml`: the deployed app is configured with a real
`DATABASE_URL` and must not silently degrade to a local store when the durable
backend is missing, so the driver always ships rather than hiding behind an
optional extra. (The lazy import is retained so `src.core.*` stays importable in
minimal/test contexts even though the driver is now always present.)

**SQLAlchemy-direct, not the `eventsourcing` library.** `eventsourcing` is built
around DDD aggregates: its `StoredEvent` is keyed on `originator_id` +
`originator_version` (a per-aggregate sequence), reads are
`select_events(originator_id, gt=version)`, and event state is opaque serialised
`bytes`. Our ledger is a *flat envelope* with a single global insertion-ordered
log and idempotency by UUID `id` — not aggregate streams. Mapping onto its
recorder would force either one synthetic aggregate per run (conflating
idempotency-by-`id` with version-by-sequence and losing a clean global order) or
opaque blobs (losing the queryable, indexed `run_id` / `kind` / `actor` columns
the ledger relies on). That is the awkward aggregate model to avoid, so the lib is
**not** a dependency. SQLAlchemy is the right level for a flat event table.

*How `eventsourcing` could layer in later:* if a scenario ever needs true DDD
aggregates (per-entity invariants, optimistic-concurrency version checks), it can
adopt `eventsourcing` *above* this store — an `ApplicationRecorder` backed by the
same Postgres — while the flat ledger remains the cross-cutting source of truth.
The two are complementary, not competing.

## Consequences

- A hosted deployment points `DATABASE_URL` at Neon
  (`postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require`) and the durable
  log lives in managed Postgres; everything else is unchanged.
- The offline path is the default and is import-clean: all existing tests pass
  with no database and no `store` extra installed.
- `snapshot_to` is backend-agnostic (it replays the log into a destination
  ledger, default a SQLite file) since Postgres has no portable in-process backup
  API like SQLite's `.backup()`; a Postgres run can be checkpointed to a portable
  file that `from_file` reopens.
- `scripts/resume_run.py` and `modal_app.py` use `DATABASE_URL` when set and fall
  back to their local SQLite file otherwise, so their offline behaviour is intact.
- Caveat: the live multi-scenario UI shares one `DATABASE_URL` across scenarios
  (one `events` table). For isolated durable runs, use `resume_run.py`, which
  keeps one database per scenario. Multi-run/multi-scenario partitioning within a
  single store (e.g. filtering by `run_id`) is a follow-up.
- `pgvector`-based episodic retrieval and Postgres `LISTEN/NOTIFY` tailing (noted
  in `docs/architecture/persistence.md`) become possible now that the durable
  backend is Postgres; neither is built here.
