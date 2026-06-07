# Memory Architecture

## The Core Insight

Agent memory is not a separate store.
It is a **filtered view over the shared append-only ledger**, computed fresh each turn.

This solves four problems at once:
- **Consistency**: memory is always in sync with the ledger — no sync bugs possible
- **Crash recovery**: reload the ledger, rebuild every memory view from scratch
- **Testability**: memory retrieval is a pure function (events → recalled events) — trivial to test
- **Privacy**: an agent's memory can only see events it was authorised to see

---

## Three Layers

### Layer 1: EpisodicMemory (always on)

The simplest layer.  An agent sees:
- Its own events (any kind, any turn)
- Globally-visible event kinds: `world.observed`, `judge.verdict`,
  `user.injected`, `run.started`, `agent.reflected`

The window is capped at `manifest.memory.window` (default 8) for small-model
context budgets.  Returns the most-recent N visible events in chronological order.

```python
class EpisodicMemory:
    agent_name: str
    max_recent: int = 8

    def visible(self, events) -> list[Event]:
        return [e for e in events if mine_or_global(e)][-max_recent:]
```

**When to use**: always.  It is the baseline memory layer and is always enabled.

---

### Layer 2: SalienceMemory (optional, manifest.memory.use_salience=True)

Replaces recency-window ranking with composite salience scoring:

```
salience(e) = w_rel·relevance(e, query) + w_rec·recency(e, turn) + w_imp·importance(e.kind)
```

| Component | How computed | Default weight |
|---|---|---|
| relevance | Jaccard similarity between event text and current scene | 0.30 |
| recency | exp(−λ·Δturn), λ=0.1 → half-life ≈7 turns | 0.40 |
| importance | Kind-based weight table | 0.30 |

**Importance weights** (from `memory.py`):

| Event kind | Weight |
|---|---|
| `user.injected` | 0.95 |
| `verdict.final` | 1.00 |
| `judge.verdict` | 0.90 |
| `agent.reflected` | 0.85 |
| `clue.found` | 0.80 |
| `world.observed` | 0.70 |
| `agent.spoke` | 0.50 |
| `agent.thought` | 0.40 |
| `run.started` | 0.30 |

Top-K events by salience score are returned in chronological order so the
prompt reads naturally (not by importance descending).

**When to use**: enable when agents run for many turns and need to surface
important but older memories over irrelevant recent ones.
First enable point: when the agent window fills up (>30 turns).

**Phase 3 upgrade**: replace keyword-Jaccard relevance with cosine similarity
over sentence embeddings (`sentence-transformers` or a lightweight embedding
model), scoring against the current scene as the query vector.

---

### Layer 3: ReflectionMemory (optional, manifest.memory.reflection_threshold=N)

Triggered when an agent has seen `N` visible events since the last reflection.
The agent is instructed to emit an `agent.reflected` event whose payload is a
high-level belief synthesising recent experience:

```
agent.reflected → {"belief": "the baker resents me", "based_on": ["evt-123", "evt-456"]}
```

Reflection events are globally visible — every agent sees them, including the
reflector itself.  This means beliefs accumulate over time without the cost
of carrying raw episodic history, and the judge can read an agent's current
belief state without full access to its memory.

**Compaction effect**: each reflection replaces N raw events with 1 belief.
After K reflections, the effective context window is `K·1 + recent_window`
instead of `N·K + recent_window`.  This is how you keep a villager coherent
over 200 turns with an 8-event context window.

**When to implement**: Phase 2 milestone.  The `ReflectionTracker` class is
already present in `src/core/memory.py` — it just needs the agent to check
`tracker.observe(events)` each turn and emit the reflection when due.

---

## Context Builder Layering

The ContextBuilder assembles layers in this order (permanent cost → variable cost):

```
IDENTITY          ← persona (never compresses)
CURRENT SCENE     ← world state from the projection
YOUR MEMORY       ← EpisodicMemory or SalienceMemory output
VISITOR           ← recent user_artifacts (last 3)
[EXTRA]           ← scenario-specific, from _build_extra_prompt()
[OUTPUT FORMAT]   ← JSON constraint (added by structured.py)
```

The layering order is deliberate:
- The model must read IDENTITY first to stay in character
- Scene before memory — what's happening now is more important than what happened before
- Visitor disturbances are always included because they are the most salient inputs
- JSON instruction is last so the model focuses on generating before being constrained

---

## Phase 3 Upgrade Path

| Feature | Phase | Mechanism |
|---|---|---|
| Keyword salience | 2 | `SalienceMemory` with Jaccard relevance |
| Reflection events | 2 | `ReflectionTracker` + `agent.reflected` kind |
| Embedding relevance | 3 | Replace Jaccard with cosine over embedding model |
| pgvector retrieval | 3 | Store event embeddings; query by ANN similarity |
| Belief graph | 4 | Structured belief store derived from reflection events |
