# ADR-0020: Optional mem0 Hosted-Cloud Backend for the Memory Index

## Status

Accepted (extends [ADR-0018](0018-layered-semantic-memory-index.md); relates to
[ADR-0019](0019-single-model-catalogue-no-cloud-path.md))

## Context

ADR-0018 added an optional **semantic memory index** as a *derived, rebuildable
lens* over the append-only ledger, behind a two-method `MemoryIndex` protocol
(`index(events)` / `search(query, k)`). ADR-0018 explicitly anticipated
**alternative backends behind the same protocol**, and ADR-0019 set the default
embedder to a **local sentence-transformers** model so an activated index stays
*off the grid* — no API key, nothing leaves the machine.

The local backend (`Mem0MemoryIndex`, wrapping the OSS `mem0.Memory`) is the right
default, but some deployments want mem0's **managed platform** (api.mem0.ai)
instead:

- no local embedder or vector store to host (mem0 runs both);
- memory that **persists across processes and runs** in one managed store, rather
  than an in-process index rebuilt per boot;
- access to mem0's hosted retrieval features and org/project scoping.

The request is "can we use the mem0 hosted cloud version." The protocol already
makes room for it; the only real design question is how to add it **without
compromising the off-the-grid default** or the ledger-is-truth invariant.

## Decision

Add a second backend, **`Mem0CloudIndex`**, that wraps the mem0 platform client
(`mem0.MemoryClient`) behind the *same* `MemoryIndex` protocol. It is **strictly
opt-in and never the default**.

**Shared base, two thin backends.** Factor the protocol surface — idempotent
upsert keyed by `event.id`, verbatim storage (`infer=False`), and search-hit →
`Event` reconstruction from metadata — into a private `_Mem0BackendBase`. The two
backends differ only in three variation points: how the client is built
(`Memory.from_config` vs `MemoryClient(...)`), how one event is stored, and how a
query is run. The invariants from ADR-0018 (derived, rebuildable, ledger is truth,
visibility filter unchanged) hold identically for both — only *where* the
embedding and retrieval happen differs.

**Same contract, verbatim storage.** The cloud backend stores each event as one
chat-style user turn with `infer=False`, so mem0's generative LLM never rewrites
or extracts — the ledger text is what is embedded, and the full event rides in
`metadata` for reconstruction. Dedup is process-local by `event.id`, *before* the
client is built, so re-indexing the same ledger slice never re-embeds and an
already-indexed id needs no network call.

**Selection by env, local stays default.** `memory_index_from_env()`:

| `MEMORY_INDEX` | `MEMORY_INDEX_BACKEND` | Result |
|---|---|---|
| unset / falsey | — | `None` — offline keyword path (default; suite default) |
| `1` / `true` / `local` / `mem0` / `on` | — | `Mem0MemoryIndex` (local, off the grid) |
| `cloud` / `mem0-cloud` / `platform` / `hosted` | — | `Mem0CloudIndex` (hosted) |
| any | `cloud` | `Mem0CloudIndex` (explicit backend wins) |

The cloud backend reads `MEM0_API_KEY` (required), and optional `MEM0_ORG_ID` /
`MEM0_PROJECT_ID` / `MEM0_HOST`, from the environment. `mem0` is lazy-imported
inside the backend, so the offline path needs neither the package nor a key, and a
missing key fails loudly on first use — not at import.

**No new dependency.** `MemoryClient` ships in the same `mem0ai` package already
declared by the optional `memory` extra; the cloud path does not need
`sentence-transformers` (embeddings are server-side).

## Off-the-grid reconciliation (ADR-0019)

ADR-0019 made the engine off the grid *by default* — local embeddings, no cloud
key path for inference or memory. This ADR does not reverse that: it adds an
**opt-in** that is dormant unless explicitly selected. **Activating
`Mem0CloudIndex` sends ledger event text to mem0's servers** — a deliberate,
clearly-flagged departure from the default, documented in the backend docstring,
`config` comments, and `memory-stack.md`. With the gate unset or set to a local
spelling, behaviour is byte-for-byte what ADR-0019 specified.

## Consequences

- **Same protocol, no blast radius.** `SalienceMemory`, `ManifestAgent._recall`,
  the registry wiring, and the agent contract are untouched — they still see a
  `MemoryIndex`. Swapping local ↔ cloud is one env var.
- **Tradeoffs the operator opts into.** Cloud means data egress (ledger text
  leaves the machine), a network dependency on api.mem0.ai, and per-call latency
  and cost on mem0's side. In exchange: managed embeddings + vector store, and
  memory that persists across processes/runs rather than rebuilding in-process.
- **Tests mirror the local tiers, all green offline.** Protocol conformance, env
  selection (each cloud spelling + backend override + credential plumbing), and
  idempotent dedup are exercised with `mem0` absent and no key. A real hosted
  round-trip is guarded behind `MEM0_API_KEY` + `MEM0_CLOUD_E2E` and skipped
  otherwise. The suite stays green with the extra uninstalled.
- **Known follow-ups.** Surface the active backend (keyword / local / cloud) on the
  stats panel; reconcile mem0's own platform memory-id with our `event.id` (we key
  dedup and reconstruction on `event.id` in metadata, not mem0's id); consider
  scoping cloud entries by `run_id` via mem0 filters for multi-run isolation
  (mirrors the ADR-0014 single-store caveat); the `infer=False` + chat-message
  `add` shape assumes a mem0 client version with platform `infer` support — pin it
  in the `memory` extra if a future release changes the contract.
