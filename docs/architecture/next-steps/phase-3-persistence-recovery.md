# Phase 3: Persistence, Crash Recovery, and Embedding Memory

> **Status: ◐ Partially realized.** SQLite ledger, `Conductor.restore()`, and
> periodic snapshots shipped (ADR-0013, `docs/architecture/long-running.md`).
> Embedding-based relevance in `SalienceMemory` and pgvector remain planned.

## Goal

Make the system survive a process kill and resume from exactly where it stopped.
Add embedding-based relevance to the salience scorer for much better episodic recall.

**Acceptance criteria**:
- Kill the process mid-run; relaunch; the scenario resumes from the last committed event.
- A run of 200 turns produces a coherent narrative (no repeated scenes, no character drift).
- The ledger file is readable without special tooling (`sqlite3 my-run.db .tables`).
- Embedding relevance in SalienceMemory scores semantically related events higher
  than keyword-only Jaccard (eval: score a set of known-related event pairs).

---

## Implementation plan

### 3.1 Swap to SQLiteLedger in the Gradio app

`SQLiteLedger` is already implemented in `src/core/sqlite_ledger.py`.
The app needs to use it when a `DB_PATH` env var is set:

```python
# In app.py
import os
from src.core.sqlite_ledger import SQLiteLedger
from src.core.ledger import Ledger

def _make_ledger() -> Ledger:
    path = os.getenv("DB_PATH")
    if path:
        return SQLiteLedger(path)
    return Ledger()   # in-memory for demo mode
```

### 3.2 Conductor turn restoration

After reopening an existing ledger, the conductor must restore its turn counter
from the max turn in the ledger:

```python
def restore_from_ledger(conductor: Conductor) -> None:
    events = conductor.ledger.events
    if events:
        conductor.run_id = events[-1].run_id
        conductor.turn = max(e.turn for e in events)
```

Add a `Conductor.restore()` method that calls this.

### 3.3 Periodic snapshots in the conductor

Add a `snapshot_every: int = 50` parameter to `Conductor`.
After every N turns, `conductor.ledger.snapshot_to(snapshot_path)`.

```python
def step(self) -> None:
    ...
    if self.snapshot_every and self.turn % self.snapshot_every == 0:
        snap_path = f"snapshots/run-{self.run_id}-turn-{self.turn:06d}.db"
        self.ledger.snapshot_to(snap_path)
```

### 3.4 Embedding-based relevance in SalienceMemory

Replace the Jaccard keyword overlap in `SalienceMemory.score()` with cosine
similarity over sentence embeddings:

```python
# Option A: sentence-transformers (runs locally, ~100MB model)
from sentence_transformers import SentenceTransformer
_embed_model = SentenceTransformer("all-MiniLM-L6-v2")  # 22M params, fast

def relevance(self, event: Event, query: str) -> float:
    ev_text = event.payload.get("text", "")
    if not ev_text or not query:
        return 0.0
    vecs = _embed_model.encode([ev_text, query])
    cos = float(vecs[0] @ vecs[1] / (np.linalg.norm(vecs[0]) * np.linalg.norm(vecs[1])))
    return max(0.0, cos)
```

```python
# Option B: OpenAI embedding API (no local model, API cost)
import openai
def embed(text: str) -> list[float]:
    return openai.embeddings.create(input=text, model="text-embedding-3-small").data[0].embedding
```

Option A is preferred (offline, fast, no API cost, under 32B cap).
The `SalienceMemory` class already accepts a configurable `relevance` function —
it just calls `self._relevance_fn(event, query)` which defaults to Jaccard.

### 3.5 pgvector upgrade path (optional in Phase 3, required in Phase 5)

Store embeddings alongside events:
```sql
ALTER TABLE events ADD COLUMN embedding vector(384);
CREATE INDEX ON events USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

Retrieval:
```sql
SELECT * FROM events ORDER BY embedding <=> $query_vec LIMIT 8;
```

This replaces the Python-side scoring loop with a DB-side ANN query.
Required when the ledger has >10,000 events and the Python loop becomes slow.

---

## Crash recovery demo script

```python
# scripts/resume_run.py
import sys
from src.core.sqlite_ledger import SQLiteLedger
from src.core.conductor import Conductor
from src.scenarios.thousand_token_wood import build_scenario

path = sys.argv[1]          # e.g. "runs/village.db"
ledger = SQLiteLedger(path)
conductor = Conductor(build_scenario(), ledger=ledger)
conductor.restore()

print(f"Resuming from turn {conductor.turn} ({len(ledger.events)} events in ledger)")

for _ in range(10):         # run 10 more turns
    conductor.step()
    print(f"Turn {conductor.turn}: {conductor.projection.current_scene[:80]}")
```

---

## New dependency: sentence-transformers (optional)

```toml
[project.optional-dependencies]
embed = ["sentence-transformers>=2.7.0", "numpy>=1.26.0"]
```

Not required — falls back to Jaccard if not installed.
Install with: `uv sync --extra embed`

---

## Files to change

| File | Change |
|---|---|
| `src/core/sqlite_ledger.py` | Already implemented — wire into app |
| `src/core/conductor.py` | Add snapshot_every, restore() method |
| `src/core/memory.py` | Replace Jaccard with pluggable relevance fn |
| `app.py` | Use SQLiteLedger when DB_PATH is set |
| `scripts/resume_run.py` | New crash-recovery demo |
| `pyproject.toml` | Add [embed] optional dependency group |

---

## Estimated effort: 2–3 days
