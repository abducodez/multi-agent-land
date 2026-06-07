# Persistence — SQLite Ledger, Snapshot, and Replay

## Why persistence matters for long-running agents

An interactive demo that runs for 5 minutes can live entirely in memory.
A village that simulates a day over 8 wall-clock hours cannot:
- The process will be restarted
- The machine will sleep
- The user will want to resume yesterday's story

The fix: make the checkpoint the ledger.  The ledger is already the source of truth.
Persisting it is the only persistence you need.

---

## SQLiteLedger

Drop-in replacement for the in-memory `Ledger`.  Same API, same idempotency,
plus durable storage:

```python
# In-memory (default, good for tests and short demos)
ledger = Ledger()

# Persistent (for long-running scenarios)
ledger = SQLiteLedger("runs/my-village.db")

# Pass to conductor
conductor = Conductor(scenario, ledger=ledger)
```

### Schema

```sql
CREATE TABLE events (
    offset         INTEGER PRIMARY KEY AUTOINCREMENT,  -- ordering
    id             TEXT UNIQUE NOT NULL,               -- idempotency key
    run_id         TEXT NOT NULL,
    turn           INTEGER NOT NULL,
    kind           TEXT NOT NULL,
    actor          TEXT NOT NULL,
    payload        TEXT NOT NULL,                      -- JSON
    created_at     TEXT NOT NULL,                      -- ISO-8601
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_run_id ON events(run_id);
CREATE INDEX idx_kind   ON events(kind);
CREATE INDEX idx_actor  ON events(actor);
```

**Key decisions**:
- `offset` is the ordering guarantee — not `created_at` (clock skew) or `turn` (duplicate turns on retry)
- `UNIQUE(id)` is the idempotency guarantee — retried workers can't double-write
- `payload` stored as JSON text — readable without schema migration for most changes
- WAL mode + NORMAL synchronous — safe on crash, fast on write

### Idempotency

Every event has a UUID `id`.  The ledger's `append()` is idempotent:
- In-memory: deduplication via `_seen_ids` set
- SQLite: `INSERT OR IGNORE` (actually `try/except IntegrityError` on `UNIQUE`)

A retried conductor step emits the same events with the same IDs → safe to replay.
This is why `uuid4()` in the event constructor is the right default (not a sequence number).

---

## Snapshot and replay

### Taking a snapshot

```python
ledger = SQLiteLedger("village.db")
# ... run for N turns ...
ledger.snapshot_to("village-snapshot-turn-100.db")
```

SQLite's `.backup()` API is atomic and zero-copy.  The snapshot is a valid,
fully-readable database file — no special restore tooling needed.

### Restoring from a snapshot

```python
ledger = SQLiteLedger.from_file("village-snapshot-turn-100.db")
conductor = Conductor(scenario, ledger=ledger)
# conductor.turn must be set from the max turn in the ledger
conductor.turn = max(e.turn for e in ledger.events)
# now step() continues from where the snapshot left off
```

### Crash recovery

After a crash, the ledger file contains all events that were committed.
Events that were computed but not yet written are lost — but idempotency means
the next retry will recompute them with the same IDs and they will be deduplicated.

The recovery pattern:
1. Open the existing ledger file (not reset).
2. Rebuild projections from the full event log.
3. Set `conductor.turn` to the max turn in the log.
4. Resume `step()` from there.

---

## Tail replay for long-running monitoring

```python
# Poll for new events since the last seen offset:
offset = 0
while True:
    new_events = ledger.tail(from_offset=offset)
    for e in new_events:
        observer.consume(e)
    offset = ledger.latest_offset()
    time.sleep(1)
```

This is the pattern for an external monitoring process that reads the ledger
without being in-process with the conductor.  Useful for:
- A separate UI process
- A dashboard that watches a running scenario
- Post-hoc analysis of a completed run

---

## Phase 3 milestone: Postgres upgrade

The `Ledger` interface is the abstraction.  Swapping SQLite for Postgres is:
1. Implement `PostgresLedger(Ledger)` using `psycopg2` + `LISTEN/NOTIFY`
2. Add `pgvector` column for embedding-based episodic retrieval
3. Use Postgres `SERIAL` for ordering, `UNIQUE(id)` for idempotency — same logic
4. Factory reads `DATABASE_URL` env var, returns the right backend

Zero scenario or agent changes.

---

## Snapshot schedule (recommended)

For a scenario running 100 turns/hour:
- Take a snapshot every 100 turns
- Keep the last 3 snapshots
- On crash, restore from the latest snapshot and replay the tail

The `scripts/` directory will grow a `snapshot_and_prune.py` in Phase 3.
