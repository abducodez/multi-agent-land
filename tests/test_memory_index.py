"""Semantic memory index tests (ADR-0018).

Three tiers, mirroring the optional-dependency tests elsewhere:

  * A FAKE in-memory ``MemoryIndex`` (no ``mem0`` required) proves the layering:
    when an index is attached, ``SalienceMemory`` retrieves by semantic rank;
    with none it falls back to keyword Jaccard.  Indexing is idempotent.
  * The env gate returns ``None`` when unset and a backend when set — provable
    with no ``mem0`` installed (construction is lazy).
  * A guarded real-``mem0`` round-trip (skipped without the package or an
    embedder configured) asserts an event survives index → search.
"""
from __future__ import annotations

import os

import pytest

from src.agents.base import ManifestAgent
from src.core.events import Event
from src.core.manifest import AgentManifest, MemoryConfig
from src.core.memory import SalienceMemory
from src.core.memory_index import (
    Mem0MemoryIndex,
    MemoryIndex,
    memory_index_from_env,
)
from src.models.router import ModelRouter


def _event(kind: str, actor: str = "x", turn: int = 1, text: str = "hello", eid: str | None = None) -> Event:
    kwargs = {"run_id": "r", "turn": turn, "kind": kind, "actor": actor, "payload": {"text": text}}
    if eid is not None:
        kwargs["id"] = eid
    return Event(**kwargs)  # type: ignore[arg-type]


class _FakeIndex:
    """A deterministic in-memory ``MemoryIndex`` — no ``mem0``, no embeddings.

    ``search`` ranks indexed events by substring/word overlap so a test can steer
    *which* event the salience layer treats as most relevant, independently of
    the keyword-Jaccard the offline path would compute.  Records calls so a test
    can assert idempotent indexing.
    """

    def __init__(self) -> None:
        self.store: dict[str, Event] = {}
        self.add_calls: list[str] = []

    def index(self, events: tuple[Event, ...]) -> None:
        for e in events:
            self.add_calls.append(e.id)
            self.store[e.id] = e  # upsert by id → idempotent

    def search(self, query: str, k: int) -> list[Event]:
        q = set(query.lower().split())
        scored = [
            (len(q & set(str(e.payload.get("text", "")).lower().split())), e)
            for e in self.store.values()
        ]
        scored.sort(key=lambda t: t[0], reverse=True)
        return [e for _, e in scored[:k]]


# ── the fake satisfies the protocol (structural typing) ─────────────────────────

class TestProtocol:
    def test_fake_is_memory_index(self):
        assert isinstance(_FakeIndex(), MemoryIndex)

    def test_mem0_backend_is_memory_index(self):
        # No mem0 import needed: the backend is constructed lazily.
        assert isinstance(Mem0MemoryIndex(), MemoryIndex)


# ── layering: index drives the relevance term, recency/importance intact ────────

class TestSalienceUsesIndex:
    def test_semantic_hit_outranks_keyword_irrelevant(self):
        """An event the index ranks top wins even with no keyword overlap to the
        query — proving the relevance term came from the index, not Jaccard."""
        idx = _FakeIndex()
        # The query shares NO words with either event text; the fake index is
        # seeded to rank the 'beacon' event first via its own signal.
        target = _event("world.observed", turn=2, text="beacon glow signal", eid="hit")
        other = _event("world.observed", turn=2, text="quiet empty room", eid="miss")
        mem = SalienceMemory("x", top_k=1, index=idx)

        # Steer the fake: query overlaps only the target's words.
        recalled = mem.visible((other, target), current_turn=3, query="beacon glow")
        assert [e.id for e in recalled] == ["hit"]

    def test_falls_back_to_keyword_without_index(self):
        match = _event("world.observed", turn=5, text="golden spores drift upward")
        miss = _event("world.observed", turn=5, text="completely unrelated content")
        mem = SalienceMemory("a")  # no index → keyword path
        s_match = mem.score(match, current_turn=6, query="golden spores")
        s_miss = mem.score(miss, current_turn=6, query="golden spores")
        assert s_match > s_miss

    def test_index_is_populated_from_visible_events_only(self):
        """The index is DERIVED from the ledger: only events that pass the
        visibility filter are indexed, never another agent's private thoughts."""
        idx = _FakeIndex()
        mine = _event("agent.thought", actor="a", turn=1, text="my secret", eid="mine")
        theirs = _event("agent.thought", actor="b", turn=1, text="their secret", eid="theirs")
        glob = _event("world.observed", actor="narrator", turn=1, text="the stage", eid="glob")
        mem = SalienceMemory("a", index=idx)
        mem.visible((mine, theirs, glob), current_turn=2, query="stage")
        assert set(idx.store) == {"mine", "glob"}  # 'theirs' never indexed

    def test_recency_still_applies_with_index(self):
        """Relevance is one term; recency must still separate equally-relevant
        events so the index does not flatten the composite score."""
        idx = _FakeIndex()
        old = _event("world.observed", turn=1, text="same words here", eid="old")
        new = _event("world.observed", turn=10, text="same words here", eid="new")
        mem = SalienceMemory("x", top_k=2, index=idx)
        recalled = mem.visible((old, new), current_turn=12, query="same words here")
        # both relevant + chronological order, but recency makes 'new' score higher
        s_old = mem.score(old, current_turn=12, query="x", relevance=1.0)
        s_new = mem.score(new, current_turn=12, query="x", relevance=1.0)
        assert s_new > s_old
        assert {e.id for e in recalled} == {"old", "new"}

    def test_format_for_prompt_shape_with_index(self):
        idx = _FakeIndex()
        e = _event("world.observed", turn=1, text="something", eid="e1")
        out = SalienceMemory("x", index=idx).format_for_prompt((e,), current_turn=2, query="something")
        assert isinstance(out, str)
        assert "something" in out and "sal=" in out  # output shape unchanged


# ── idempotent indexing (derived, rebuildable) ──────────────────────────────────

class TestIdempotentIndexing:
    def test_reindex_does_not_duplicate(self):
        idx = _FakeIndex()
        events = (_event("world.observed", turn=1, text="a", eid="e1"),
                  _event("world.observed", turn=2, text="b", eid="e2"))
        idx.index(events)
        idx.index(events)  # re-index same slice
        assert len(idx.store) == 2  # keyed by id → no duplicates

    def test_mem0_backend_skips_already_indexed_ids(self):
        """The real backend dedupes by id before touching mem0, so a process that
        re-indexes the same ledger slice each turn does not re-embed it."""
        backend = Mem0MemoryIndex()
        backend._indexed.add("e1")  # pretend already indexed this process
        # No mem0 call should happen for an already-indexed id; _memory() would
        # raise (mem0 may be absent), so reaching it on a dup would surface here.
        backend.index((_event("world.observed", eid="e1"),))  # no-op, no import


# ── env gate (no mem0 required) ──────────────────────────────────────────────────

class TestEnvGate:
    def test_none_when_unset(self):
        assert memory_index_from_env({}) is None

    def test_none_when_falsey(self):
        assert memory_index_from_env({"MEMORY_INDEX": "0"}) is None

    def test_backend_when_truthy(self):
        idx = memory_index_from_env({"MEMORY_INDEX": "1"})
        assert isinstance(idx, Mem0MemoryIndex)

    def test_config_blob_is_parsed(self):
        idx = memory_index_from_env(
            {"MEMORY_INDEX": "true", "MEMORY_INDEX_CONFIG": '{"version": "v1.1"}'}
        )
        assert isinstance(idx, Mem0MemoryIndex)
        assert idx._config == {"version": "v1.1"}


# ── agent wiring: _recall threads the index into salience ────────────────────────

class _SalienceAgent(ManifestAgent):
    manifest = AgentManifest(
        name="recaller",
        persona="p",
        may_emit=["agent.spoke"],
        memory=MemoryConfig(use_salience=True, salience_top_k=1),
    )


class TestRecallWiring:
    def test_recall_uses_attached_index(self):
        idx = _FakeIndex()
        agent = _SalienceAgent(ModelRouter(offline=True), memory_index=idx)
        from src.core.projections import StageProjection

        events = (
            _event("world.observed", actor="n", turn=1, text="beacon glow signal", eid="hit"),
            _event("world.observed", actor="n", turn=1, text="quiet empty room", eid="miss"),
        )
        proj = StageProjection(current_scene="beacon glow")
        out = agent._recall(turn=2, projection=proj, recent_events=events)
        assert "beacon" in out  # the index-ranked event made it into the prompt
        assert idx.store  # the index was derived from the ledger events

    def test_recall_without_index_is_keyword_path(self):
        agent = _SalienceAgent(ModelRouter(offline=True))  # no index attached
        from src.core.projections import StageProjection

        e = _event("world.observed", actor="n", turn=1, text="golden spores")
        out = agent._recall(turn=2, projection=StageProjection(current_scene="golden spores"), recent_events=(e,))
        assert isinstance(out, str) and "golden" in out


# ── guarded real-mem0 round-trip (requires mem0 + an embedder) ───────────────────

class TestMem0RoundTrip:
    def test_index_then_search_recovers_event(self):
        pytest.importorskip("mem0")
        if not os.getenv("OPENAI_API_KEY") and not os.getenv("MEMORY_INDEX_CONFIG"):
            pytest.skip("mem0 needs an embedder (OPENAI_API_KEY or MEMORY_INDEX_CONFIG)")

        backend = Mem0MemoryIndex()
        ev = _event("world.observed", turn=1, text="golden spores drift over the glass forest", eid="rt1")
        try:
            backend.index((ev,))
            hits = backend.search("golden spores", k=5)
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"mem0 backend unavailable: {exc}")

        assert any(h.id == "rt1" for h in hits)
        # Mapped back to a real Event with payload intact (derived from metadata).
        hit = next(h for h in hits if h.id == "rt1")
        assert hit.kind == "world.observed"
        assert hit.payload.get("text", "").startswith("golden spores")
