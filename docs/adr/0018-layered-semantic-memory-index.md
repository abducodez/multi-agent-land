# ADR-0018: Semantic Memory Index as a Layered, Derived Ledger View

## Status

Accepted. **Amended by [ADR-0019](0019-single-model-catalogue-no-cloud-path.md):**
the default embedder is now local sentence-transformers (no `OPENAI_API_KEY`), so
an activated index is fully off the grid; `MEMORY_INDEX_CONFIG` still overrides it.

## Context

Agent memory is a **filtered view over the append-only event ledger**, not a
separate store (ADR-0005). `EpisodicMemory` returns the most-recent visible
events; `SalienceMemory` (ADR-0005 consequences, `docs/architecture/memory-stack.md`)
ranks visible events by a composite score:

```
salience(e) = w_rel·relevance(e, query) + w_rec·recency(e, turn) + w_imp·importance(e.kind)
```

The `relevance` term is **keyword (Jaccard) overlap** between the event text and
the current scene. That is cheap and deterministic but lexical: it misses an
event that is *about* the same thing in different words, exactly the case where
recall matters most over a long run. The documented Phase-3 upgrade is to replace
keyword relevance with semantic similarity over an embedding model, with vectors
optionally living in the durable Postgres/pgvector store from ADR-0014.

The constraint is the same one that has held since ADR-0001/0005: the ledger is
the **single source of truth**. A vector store must not become a second, parallel
store on a write path the ledger does not own. If it is wiped, the ledger must be
able to rebuild it. And, like every other optional integration here
(ADR-0014/0015/0016/0017), the offline path must stay the default the suite
exercises — no network, no credentials, and no new package required to
`import src.*` or `import app`.

## Decision

Add a **semantic retrieval index as a derived, rebuildable lens over the ledger**,
used only to compute the `relevance` term. The index changes *how* relevance is
scored, never *which* events are eligible nor the recency/importance terms.

**A small protocol.** `MemoryIndex` (`src/core/memory_index.py`) is two methods:
`index(events) -> None` derives/refreshes entries from ledger events, and
`search(query, k) -> list[Event]` returns the most relevant indexed events. It is
a `runtime_checkable` `Protocol`, so any backend — a vector service, a local
embedding store, or a test fake — supplies semantic relevance without the salience
layer knowing which.

**Derived, not authoritative.** `index()` upserts each event under its
`event.id`, so re-indexing the same ledger slice each turn is idempotent (no
duplicates) and the index is rebuildable from the ledger at any time. The salience
layer always **derives then reads**: it indexes the visible candidates first, then
queries, so the index can never report an event the ledger has not produced. The
event is reconstructed from metadata stored on the entry, so a hit needs no second
lookup. This is what keeps the index a *faster lens on the same events* (ADR-0005)
rather than a competing store.

**Layered into salience, visibility intact.** `SalienceMemory` gains an optional
`index` field. With an index attached, the relevance term is derived from the
semantic search rank (normalised to `[0,1]` by descending rank); with `index=None`
(the default) it is the keyword-Jaccard path, unchanged byte-for-byte. In both
cases the candidate set is the *same* ledger-derived visibility filter
(`actor == self.agent_name or kind in _GLOBALLY_VISIBLE`) and the recency and
importance terms are untouched — an agent never recalls another agent's private
thoughts, with or without the index. `_recall` in `src/agents/base.py` threads the
agent's optional `memory_index` into `SalienceMemory`; `format_for_prompt` output
shape is unchanged (`[turn][kind][sal=…] text`).

**The concrete backend.** `Mem0MemoryIndex` wraps a vector-memory library, lazily
imported inside the backend so the package is touched only when the index is
exercised. Each event is stored as one raw memory with **inference disabled** —
the event text is embedded verbatim, with **no model-driven fact extraction** — so
indexing is deterministic and the ledger, not a model, remains the source of
truth. The full event rides along in the entry metadata for reconstruction.

**Env-gated, offline by default.** `memory_index_from_env()` returns `None` unless
`MEMORY_INDEX` is truthy, in which case it builds the backend (still not importing
the library until first use). An embedding model is required when the index is
active; by default embeddings route via `OPENAI_API_KEY` — the same credential the
live model path already uses — and `MEMORY_INDEX_CONFIG` (a JSON blob forwarded to
the library's `from_config`) can pin a local embedder or persist vectors in the
project's own Postgres/pgvector (ADR-0014), so the index can live beside the ledger
it derives from. With the gate unset the system stays on the keyword path and never
imports the package.

**Dependency.** `mem0ai` is a new optional `memory` extra in `pyproject.toml`. It
is lazy-imported, so `import src.*` and `import app` work with it not installed and
the gate unset — the offline default the test-suite exercises.

## Reconciliation with ADR-0005

ADR-0005 makes memory a pure function of the ledger and explicitly anticipates this
step: *"Richer retrieval (semantic search, salience scoring) can be added later as
an upgraded `EpisodicMemory` implementation without changing the agent protocol."*
This ADR honours that literally. The index is **derived** (populated from ledger
events, keyed by `event.id`), **rebuildable** (wipe it and re-index from the
ledger), and **non-authoritative** (it only re-ranks the relevance term over events
the visibility filter already admits). No event originates in the index; nothing is
written there that the ledger does not own first. The four ADR-0005 properties hold:
consistency (the index trails the ledger and is rebuilt from it), crash recovery
(reload the ledger, re-index), testability (a fake `MemoryIndex` makes the semantic
path deterministic and offline), and privacy (the candidate filter is unchanged, so
an agent still cannot recall another's private thoughts).

## Consequences

- With `MEMORY_INDEX` unset the relevance term is keyword-Jaccard exactly as before:
  `tests/test_memory.py` and `tests/test_salience_memory.py` are unchanged and green,
  and the suite stays ≥243 green offline. With the package absent the one real-backend
  test skips (`pytest.importorskip`); the fake-index tests exercise the layering,
  idempotency, env gate, and `_recall` wiring with nothing installed.
- The index is a derived view, not a second source of truth: it is keyed by
  `event.id` (idempotent re-index), populated from the ledger before each query, and
  can be dropped and rebuilt from the ledger. It never sits on a write path the ledger
  does not own.
- Only the relevance term changes. Recency, importance, the top-K cut, chronological
  ordering, and the `format_for_prompt` shape are identical across both paths, so a
  scenario that enables the index sees better recall without other behavioural drift.
- Persisting vectors in the ADR-0014 Postgres/pgvector store is an optional
  `MEMORY_INDEX_CONFIG` path, not a requirement; the default in-process vector store
  works offline-with-embedder for a single process.
- **Alternative backends behind the same protocol.** `MemoryIndex` is deliberately
  two methods, so a stateful agent-memory service (e.g. a Letta-style memory server)
  could be wrapped as another `MemoryIndex` implementation — `index()` writing through
  to it, `search()` reading back — without touching `SalienceMemory` or the agent
  protocol, as long as it too treats the ledger as authoritative and stays rebuildable.
- Follow-ups: surface the active relevance mode (keyword vs semantic) on the stats
  panel; add a one-shot "rebuild index from ledger" path for cold start / after a wipe;
  evaluate scoping vector entries by `run_id` for multi-run isolation (mirrors the
  ADR-0014 single-store caveat); blend semantic and lexical relevance rather than
  switching between them.
