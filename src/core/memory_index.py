"""Retrieval index for the salience *relevance* term — a derived ledger lens.

The append-only event ledger is the single source of truth (ADR-0005). This
module adds an optional **semantic** retrieval index *over* that ledger: it does
not store anything the ledger does not already own, and it can be wiped and
rebuilt from the ledger at any time. It is a faster lens on the same events, not
a second store (ADR-0018).

Two pieces:

  * :class:`MemoryIndex` — a tiny protocol the salience layer can lean on:
    ``index(events)`` derives vector entries from ledger events (idempotent,
    keyed by ``event.id``) and ``search(query, k)`` returns the most relevant
    events back. Any backend that satisfies this protocol can supply semantic
    relevance — a vector service, a local embedding store, or a fake in tests.

  * :class:`Mem0MemoryIndex` — a concrete backend. It is **lazy-imported and
    env-gated**: with the backend not installed or not configured, nothing here
    is imported and :class:`~src.core.memory.SalienceMemory` falls back to its
    keyword-Jaccard relevance exactly as before. The backend activates only when
    :func:`memory_index_from_env` finds it configured.

Because the index is derived, ``index()`` upserts each event under its
``event.id`` so re-indexing the same events is a no-op (no duplicates) — this is
what makes the index rebuildable rather than authoritative.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from src.core.events import Event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mem0 import Memory

#: Env gate. Set to a truthy value to activate the semantic index; unset (the
#: default) keeps memory on the offline keyword path with nothing imported.
INDEX_ENV = "MEMORY_INDEX"

#: Truthy spellings accepted for the gate and boolean sub-options.
_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on", "mem0"})


# ── protocol ──────────────────────────────────────────────────────────────────

@runtime_checkable
class MemoryIndex(Protocol):
    """A derived, rebuildable semantic index over ledger events.

    Implementations MUST treat the ledger as authoritative: ``index`` is an
    idempotent upsert keyed by ``event.id`` (re-indexing never duplicates), and
    ``search`` only ever returns events that were previously indexed.
    """

    def index(self, events: tuple[Event, ...]) -> None:
        """Derive/refresh index entries for *events* (idempotent by ``event.id``)."""
        ...

    def search(self, query: str, k: int) -> list[Event]:
        """Return up to *k* indexed events most semantically relevant to *query*."""
        ...


# ── mem0 backend ────────────────────────────────────────────────────────────

def _event_text(event: Event) -> str:
    """The natural-language surface of an event used for embedding/recall."""
    return str(event.payload.get("text") or event.payload.get("summary") or event.payload)


class Mem0MemoryIndex:
    """Semantic :class:`MemoryIndex` backed by the ``mem0`` vector memory.

    Derived, not authoritative. Each ledger event is upserted as one raw memory
    (``infer=False`` — text is stored verbatim, **no model extraction**, so
    indexing is deterministic and the ledger stays the source of truth) carrying
    the full event in ``metadata`` so a search hit reconstructs the original
    :class:`Event` without a second lookup. The entry id is the ``event.id``, so
    re-indexing the same event updates in place rather than duplicating — the
    index is rebuildable from the ledger.

    Configuration (env, read by :func:`memory_index_from_env`):

      * ``MEMORY_INDEX`` — gate; truthy activates the index, unset disables it.
      * Embedder credentials — an embedding model is required to vectorise event
        text. By default ``mem0`` routes embeddings via ``OPENAI_API_KEY`` (the
        same key the live model path already uses); point it elsewhere with a
        ``MEMORY_INDEX_CONFIG`` JSON blob (passed verbatim to ``mem0`` as its
        config — see its docs for ``embedder`` / ``vector_store`` keys).
      * ``MEMORY_INDEX_CONFIG`` — optional JSON config forwarded to
        ``mem0.Memory.from_config``. Use it to pin a local embedder or to persist
        vectors in the project's own Postgres/pgvector (the durable store from
        ADR-0014) instead of the default in-process vector store, so the index
        lives beside the ledger it derives from.

    ``mem0`` is imported lazily inside :meth:`_memory` so ``import src.*`` and
    ``import app`` work with the package not installed.
    """

    #: mem0 scopes memories to a session id; the index is engine-wide, so a fixed
    #: namespace keeps every event in one searchable space.
    _NAMESPACE = "ledger"

    def __init__(self, config: dict | None = None) -> None:
        self._config = config
        self._mem: "Memory | None" = None
        self._indexed: set[str] = set()

    # ── lazy construction ─────────────────────────────────────────────────────

    def _memory(self) -> "Memory":
        """Construct (once) and return the underlying ``mem0`` memory."""
        if self._mem is None:
            from mem0 import Memory  # lazy: offline import must not require mem0

            self._mem = (
                Memory.from_config(self._config) if self._config else Memory()
            )
        return self._mem

    # ── MemoryIndex protocol ──────────────────────────────────────────────────

    def index(self, events: tuple[Event, ...]) -> None:
        """Upsert *events* into the vector store, keyed by ``event.id``.

        Idempotent: an ``event.id`` already indexed in this process is skipped, so
        re-indexing the same ledger slice each turn does not duplicate entries.
        """
        fresh = [e for e in events if e.id not in self._indexed]
        if not fresh:
            return
        mem = self._memory()
        for event in fresh:
            mem.add(
                _event_text(event),
                user_id=self._NAMESPACE,
                metadata=_event_metadata(event),
                infer=False,  # store verbatim; the ledger, not a model, is truth
            )
            self._indexed.add(event.id)

    def search(self, query: str, k: int) -> list[Event]:
        """Semantic search; map hits back to :class:`Event` via stored metadata."""
        if not query or k <= 0:
            return []
        mem = self._memory()
        hits = mem.search(query, top_k=k, filters={"user_id": self._NAMESPACE})
        events: list[Event] = []
        for hit in _result_items(hits):
            event = _event_from_metadata(hit.get("metadata"))
            if event is not None:
                events.append(event)
        return events


# ── metadata round-trip (event ⇄ vector entry) ────────────────────────────────

def _event_metadata(event: Event) -> dict:
    """Flatten an event into JSON-safe metadata for the vector entry."""
    return {
        "event_id": event.id,
        "run_id": event.run_id,
        "turn": event.turn,
        "kind": event.kind,
        "actor": event.actor,
        "payload": event.payload,
        "created_at": event.created_at.isoformat(),
        "schema_version": event.schema_version,
    }


def _event_from_metadata(metadata: dict | None) -> Event | None:
    """Reconstruct an :class:`Event` from stored metadata, or ``None`` if absent."""
    if not metadata or "event_id" not in metadata:
        return None
    try:
        return Event(
            id=str(metadata["event_id"]),
            run_id=str(metadata.get("run_id", "")),
            turn=int(metadata.get("turn", 0)),
            kind=str(metadata["kind"]),
            actor=str(metadata.get("actor", "")),
            payload=dict(metadata.get("payload") or {}),
            schema_version=int(metadata.get("schema_version", 1)),
        )
    except (KeyError, ValueError, TypeError):  # pragma: no cover - defensive
        return None


def _result_items(hits: object) -> list[dict]:
    """Normalise ``mem0.search`` output to a list of hit dicts.

    ``mem0`` returns either ``{"results": [...]}`` (v1.1+) or a bare list,
    depending on version/config; accept both so the backend is version-tolerant.
    """
    if isinstance(hits, dict):
        results = hits.get("results", [])
    else:
        results = hits
    return [h for h in results if isinstance(h, dict)] if isinstance(results, list) else []


# ── env gate ───────────────────────────────────────────────────────────────────

def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def memory_index_from_env(env: dict[str, str] | None = None) -> MemoryIndex | None:
    """Build a :class:`Mem0MemoryIndex` from the env gate, or ``None`` if unset.

    Returns ``None`` (the offline default the suite exercises) unless
    ``MEMORY_INDEX`` is truthy. ``mem0`` is only imported later, on first use, so
    a truthy gate without the package installed still imports cleanly and fails
    loudly only when the index is actually exercised.
    """
    source = os.environ if env is None else env
    if not _is_truthy(source.get(INDEX_ENV)):
        return None
    raw_config = (source.get("MEMORY_INDEX_CONFIG") or "").strip()
    config: dict | None = None
    if raw_config:
        import json

        config = json.loads(raw_config)
    return Mem0MemoryIndex(config=config)
