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

  * Two concrete backends behind that protocol, both **lazy-imported and
    env-gated** so nothing is imported and :class:`~src.core.memory.SalienceMemory`
    stays on its keyword-Jaccard relevance unless an index is configured:

      - :class:`Mem0MemoryIndex` — the default, **off the grid**. Wraps the
        ``mem0`` OSS ``Memory`` with a local sentence-transformers embedder; no
        API key, nothing leaves the machine (ADR-0019).
      - :class:`Mem0CloudIndex` — opt-in hosted backend. Wraps the ``mem0``
        platform ``MemoryClient`` (api.mem0.ai). **Activating it sends ledger
        event text to mem0's servers** and needs ``MEM0_API_KEY`` — a deliberate
        departure from the off-the-grid default, so it is never the default
        (ADR-0020).

Because the index is derived, both backends upsert each event under its
``event.id`` (process-local dedup) so re-indexing the same events is a no-op (no
duplicates) — this is what makes the index rebuildable rather than authoritative.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from src import observability as obs
from src.core.events import Event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mem0 import Memory, MemoryClient

#: Env gate. Set to a truthy value to activate the local semantic index, or to a
#: cloud spelling (see :data:`_CLOUD_VALUES`) for the hosted backend; unset (the
#: default) keeps memory on the offline keyword path with nothing imported.
INDEX_ENV = "MEMORY_INDEX"

#: Optional explicit backend selector (``local`` | ``cloud``). Takes precedence
#: over the spelling of ``MEMORY_INDEX`` when set.
BACKEND_ENV = "MEMORY_INDEX_BACKEND"

#: Truthy spellings accepted for the gate and boolean sub-options (→ local backend).
_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on", "mem0", "local"})

#: Spellings of ``MEMORY_INDEX`` that select the hosted mem0 platform backend.
_CLOUD_VALUES: frozenset[str] = frozenset({"cloud", "mem0-cloud", "platform", "hosted"})

#: Default mem0 config when ``MEMORY_INDEX_CONFIG`` is unset: embed LOCALLY with
#: sentence-transformers (no API key; fully offline once the model is cached), so
#: the active index stays off the grid like the rest of the engine. The index
#: stores ledger events verbatim (``infer=False``) and search embeds the query
#: locally, so mem0's generative LLM is never invoked — the placeholder key just
#: keeps its construction from demanding a cloud credential. Any of this can be
#: overridden with a ``MEMORY_INDEX_CONFIG`` JSON blob (passed verbatim to mem0).
_LOCAL_INDEX_CONFIG: dict = {
    "embedder": {
        "provider": "huggingface",
        "config": {"model": "sentence-transformers/all-MiniLM-L6-v2"},
    },
    "llm": {"provider": "openai", "config": {"api_key": "EMPTY"}},
}


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


# ── shared event ⇄ entry helpers ──────────────────────────────────────────────


def _event_text(event: Event) -> str:
    """The natural-language surface of an event used for embedding/recall."""
    return str(event.payload.get("text") or event.payload.get("summary") or event.payload)


# ── shared mem0 backend base ──────────────────────────────────────────────────


class _Mem0BackendBase:
    """Shared :class:`MemoryIndex` machinery for the mem0-backed indexes.

    Subclasses supply the three variation points — how the client is built and
    how a single event is stored / queried — while this base owns the protocol
    surface that keeps the index *derived*: idempotent upsert keyed by
    ``event.id`` and search-hit → :class:`Event` reconstruction from metadata.
    """

    #: mem0 scopes memories to an id; the index is engine-wide, so a fixed
    #: namespace keeps every event in one searchable space.
    _NAMESPACE = "ledger"

    def __init__(self) -> None:
        self._mem: object | None = None
        self._indexed: set[str] = set()

    # ── variation points (subclass) ───────────────────────────────────────────

    def _build_memory(self) -> object:
        """Construct the underlying mem0 client (lazy-imported by the subclass)."""
        raise NotImplementedError

    def _store(self, mem: object, event: Event) -> None:
        """Upsert one event verbatim into *mem* (``infer=False``; ledger is truth)."""
        raise NotImplementedError

    def _query(self, mem: object, query: str, k: int) -> list[dict]:
        """Run semantic search on *mem*; return raw hit dicts (carrying metadata)."""
        raise NotImplementedError

    # ── lazy construction ─────────────────────────────────────────────────────

    def _memory(self) -> object:
        if self._mem is None:
            self._mem = self._build_memory()
        return self._mem

    # ── MemoryIndex protocol ──────────────────────────────────────────────────

    def index(self, events: tuple[Event, ...]) -> None:
        """Upsert *events*, keyed by ``event.id`` — idempotent within the process.

        Dedup happens *before* the client is built, so re-indexing the same
        ledger slice each turn never re-embeds and never forces a mem0 import."""
        fresh = [e for e in events if e.id not in self._indexed]
        if not fresh:
            return
        mem = self._memory()
        for event in fresh:
            self._store(mem, event)
            self._indexed.add(event.id)

    def search(self, query: str, k: int) -> list[Event]:
        """Semantic search; map hits back to :class:`Event` via stored metadata."""
        if not query or k <= 0:
            return []
        with obs.span(
            "memory.index.search",
            **{"memory.query": query, "memory.k": k, "memory.backend": type(self).__name__},
        ):
            started = time.perf_counter()
            mem = self._memory()
            events: list[Event] = []
            for hit in self._query(mem, query, k):
                event = _event_from_metadata(hit.get("metadata"))
                if event is not None:
                    events.append(event)
            elapsed_ms = (time.perf_counter() - started) * 1000
            obs.add_span_attrs(**{"memory.hits": len(events), "memory.latency_ms": round(elapsed_ms, 2)})
            obs.observe("memory.index.hits", len(events))
            obs.observe("memory.index.latency_ms", elapsed_ms)
            obs.log(
                "memory.index.search",
                level="debug",
                backend=type(self).__name__,
                query=query,
                k=k,
                hits=len(events),
                latency_ms=round(elapsed_ms, 2),
            )
            return events


# ── local (off-the-grid) backend ──────────────────────────────────────────────


class Mem0MemoryIndex(_Mem0BackendBase):
    """Local semantic :class:`MemoryIndex` backed by the ``mem0`` OSS ``Memory``.

    Derived, not authoritative, and **off the grid**: each ledger event is
    upserted as one raw memory (``infer=False`` — text stored verbatim, **no
    model extraction**) carrying the full event in ``metadata`` so a search hit
    reconstructs the :class:`Event` without a second lookup. Embeddings run
    locally via sentence-transformers by default (:data:`_LOCAL_INDEX_CONFIG`).

    Configuration (env, read by :func:`memory_index_from_env`):

      * ``MEMORY_INDEX`` — gate; truthy (``1``/``true``/``local``/…) activates this
        backend, unset disables it.
      * ``MEMORY_INDEX_CONFIG`` — optional JSON config forwarded verbatim to
        ``mem0.Memory.from_config``, replacing the local default (pick a different
        embedder, or persist vectors in the project's Postgres/pgvector, ADR-0014).

    ``mem0`` is imported lazily inside :meth:`_build_memory` so ``import src.*`` and
    ``import app`` work with the package not installed.
    """

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        self._config = config

    def _build_memory(self) -> "Memory":
        from mem0 import Memory  # lazy: offline import must not require mem0

        return Memory.from_config(self._config or _LOCAL_INDEX_CONFIG)

    def _store(self, mem: object, event: Event) -> None:
        mem.add(  # type: ignore[attr-defined]
            _event_text(event),
            user_id=self._NAMESPACE,
            metadata=_event_metadata(event),
            infer=False,  # store verbatim; the ledger, not a model, is truth
        )

    def _query(self, mem: object, query: str, k: int) -> list[dict]:
        return _result_items(mem.search(query, top_k=k, filters={"user_id": self._NAMESPACE}))  # type: ignore[attr-defined]


# ── hosted (opt-in) backend ────────────────────────────────────────────────────


class Mem0CloudIndex(_Mem0BackendBase):
    """Hosted semantic :class:`MemoryIndex` backed by the ``mem0`` platform.

    Wraps ``mem0.MemoryClient`` (api.mem0.ai): embeddings, the vector store, and
    retrieval all live in mem0's managed service. The :class:`MemoryIndex`
    contract is identical to the local backend — derived, idempotent, ledger is
    truth — and events are still stored verbatim (``infer=False``) with the full
    event in ``metadata`` for reconstruction. The only difference is *where* the
    work happens.

    **Off-the-grid caveat (ADR-0019/0020).** Activating this backend sends ledger
    event text to mem0's servers and requires a ``MEM0_API_KEY``. It is therefore
    strictly opt-in and never the default; the local backend remains the engine's
    off-the-grid default.

    Configuration (env, read by :func:`memory_index_from_env`):

      * ``MEMORY_INDEX=cloud`` (or ``MEMORY_INDEX_BACKEND=cloud``) — selects this
        backend.
      * ``MEM0_API_KEY`` — required platform key (falls back to the client's own
        ``MEM0_API_KEY`` env read if not passed explicitly).
      * ``MEM0_ORG_ID`` / ``MEM0_PROJECT_ID`` / ``MEM0_HOST`` — optional scoping.

    ``mem0`` is imported lazily inside :meth:`_build_memory`, so the offline path
    needs neither the package nor a key.
    """

    def __init__(
        self,
        api_key: str | None = None,
        org_id: str | None = None,
        project_id: str | None = None,
        host: str | None = None,
    ) -> None:
        super().__init__()
        self._api_key = api_key
        self._org_id = org_id
        self._project_id = project_id
        self._host = host

    def _build_memory(self) -> "MemoryClient":
        from mem0 import MemoryClient  # lazy: offline import must not require mem0

        # Pass only what is set; MemoryClient falls back to MEM0_API_KEY from the
        # environment and raises loudly here (not at import) if no key is found.
        kwargs = {
            k: v
            for k, v in {
                "api_key": self._api_key,
                "org_id": self._org_id,
                "project_id": self._project_id,
                "host": self._host,
            }.items()
            if v
        }
        return MemoryClient(**kwargs)

    def _store(self, mem: object, event: Event) -> None:
        # The platform `add` takes chat-style messages; one verbatim user turn per
        # event, inference disabled so nothing but the ledger text is stored.
        mem.add(  # type: ignore[attr-defined]
            [{"role": "user", "content": _event_text(event)}],
            user_id=self._NAMESPACE,
            metadata=_event_metadata(event),
            infer=False,
        )

    def _query(self, mem: object, query: str, k: int) -> list[dict]:
        return _result_items(mem.search(query, top_k=k, filters={"user_id": self._NAMESPACE}))  # type: ignore[attr-defined]


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
    """Normalise mem0 ``search`` output to a list of hit dicts.

    Both the OSS ``Memory`` and the platform ``MemoryClient`` return either
    ``{"results": [...]}`` or a bare list depending on version/config; accept both
    so the backends are version-tolerant.
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
    """Build a mem0-backed :class:`MemoryIndex` from the env, or ``None`` if unset.

    Selection (``mem0`` is only imported later, on first use):

      * gate unset / falsey → ``None`` (the offline keyword path the suite exercises).
      * ``MEMORY_INDEX`` truthy (``1``/``true``/``local``/…) → :class:`Mem0MemoryIndex`
        (local sentence-transformers; off the grid).
      * ``MEMORY_INDEX`` ∈ {cloud, mem0-cloud, platform, hosted}, or
        ``MEMORY_INDEX_BACKEND=cloud`` → :class:`Mem0CloudIndex` (hosted; sends
        ledger text to mem0). An explicit ``MEMORY_INDEX_BACKEND`` wins over the
        ``MEMORY_INDEX`` spelling.
    """
    source = os.environ if env is None else env
    gate = (source.get(INDEX_ENV) or "").strip().lower()
    backend = (source.get(BACKEND_ENV) or "").strip().lower()

    is_cloud = backend == "cloud" or gate in _CLOUD_VALUES
    if not (is_cloud or _is_truthy(gate)):
        return None

    if is_cloud:
        return Mem0CloudIndex(
            api_key=source.get("MEM0_API_KEY") or None,
            org_id=source.get("MEM0_ORG_ID") or None,
            project_id=source.get("MEM0_PROJECT_ID") or None,
            host=source.get("MEM0_HOST") or None,
        )

    raw_config = (source.get("MEMORY_INDEX_CONFIG") or "").strip()
    config: dict | None = None
    if raw_config:
        import json

        config = json.loads(raw_config)
    return Mem0MemoryIndex(config=config)
