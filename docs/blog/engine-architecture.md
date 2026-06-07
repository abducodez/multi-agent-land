# How We Built One Engine and Let It Wear Three Costumes

*Technical blog — Multi-Agent Land, Thousand Token Wood hackathon*

---

## The Thesis

When you look at a village of quirky AI characters, a murder-mystery swarm, and a
collaborative illustrated serial side-by-side, you might think you're looking at three
different systems.  You're not.  They are **the same four abstractions** wearing different
configurations:

1. An **append-only event ledger** — the one source of truth
2. A **conductor** — schedules who acts, enforces budgets, drives the loop
3. **Agents** — stateless functions that read context and emit a single typed event
4. **Projections** — pure functions that fold the event stream into any view you need

Everything else is configuration.

---

## The Event Ledger: Why Append-Only?

The ledger is the spine.  Agents don't call each other — they append events
(`world.observed`, `agent.spoke`, `judge.verdict`) and subscribe to the event types they
care about.  No direct coupling.  No shared mutable state.  No race conditions.

```
[run.started ] conductor    {"seed": "A village of stage props..."}
[world.observed] seedkeeper  {"text": "A mossy ticket booth opens in a tree root."}
[judge.verdict ] critic      {"text": "Keep it — specific and playable."}
[agent.spoke  ] pocket-actor {"text": "I am collecting echoes to knit a ladder to the moon."}
[user.injected ] visitor     {"text": "A lantern starts whispering recipes."}
```

Every row is immutable.  The UI, the stats panel, the agent memory, and the judge's
analysis are all **projections derived from this log**.  That means:

- **Crash recovery is free**: reload the ledger, rebuild every projection from scratch.
- **Testing is trivial**: projections are pure functions.  Give them a list of events,
  assert the output.  No mocks, no shared state.
- **The system is observable by default**: the ledger *is* the audit trail.

---

## Memory Without a Memory Store

The most common question we get: where does each agent store its memory?

Nowhere new.  Agent memory is a **filtered view over the shared ledger**, recomputed
each turn by `EpisodicMemory`:

```python
class EpisodicMemory:
    def visible(self, events: tuple[Event, ...]) -> list[Event]:
        result = []
        for e in events:
            if e.actor == self.agent_name or e.kind in self._visible_kinds:
                result.append(e)
        return result[-self.max_recent:]
```

The Seedkeeper sees its own actions plus world events.  The Pocket Actor sees world
events plus visitor injections.  Neither can read the other's private thoughts.
The window is capped at 8 events to stay within small-model context budgets.

This is event sourcing plus CQRS in its simplest form: one write side (the ledger),
many read sides (each agent's memory projection, the UI's stage view, the stats panel).

---

## The Context Builder: Prompt Assembly as a Separate Concern

Before this pattern, each agent owned its own prompt string.  After ten agents, the
variation was unmanageable and inconsistent.  Now there is one place:

```python
class ContextBuilder:
    def build(self, *, agent_name, persona, projection, all_events) -> str:
        memory = EpisodicMemory(agent_name).format_for_prompt(all_events)
        return (
            f"IDENTITY\n{persona}\n\n"
            f"CURRENT SCENE\n{projection.current_scene}\n\n"
            f"YOUR MEMORY (recent events you witnessed)\n{memory}\n\n"
            f"VISITOR DISTURBANCES\n{visitor_lines}"
        )
```

Agents are responsible for the **persona string** and the **event they emit**.
The builder owns the layering order.  Adding a new memory layer (reflection summaries,
salience scoring) touches one file, not every agent.

---

## The Governor: Budget Before It Bites You

Small models are cheap per call.  Many agents calling many times for many hours is not.
The `Governor` enforces three caps:

- `max_turns` — the conductor raises the curtain at most this many times
- `max_calls_per_turn` — no single turn can trigger more than N model calls
- `max_total_calls` — the whole run cannot exceed M calls

The conductor checks the governor before every scheduled agent.  `BudgetExceeded` is a
named exception the UI surfaces gracefully rather than burning quota silently.

---

## Two Scenarios, Zero Engine Edits

The proof that the abstraction works is the second scenario.

**Thousand Token Wood** is world-growth: the scene gets stranger turn by turn, a
judge critiques it, a character speaks their impossible want, an echo transforms
visitor injections.  The scheduling is round-robin with variation.

**Mystery Roots** is convergence: a mystery is stated, a clue-gatherer extracts
evidence, a hypothesis-former proposes an explanation, a devil's advocate challenges
it, and a judge declares the verdict.  The scheduling is a 4-phase cycle.

Same conductor.  Same ledger.  Same governor.  Same context builder.  Same memory.
**Different cast, different schedule, different cognitive shape.**

The engine is just plumbing.  The scenario is pure config.

---

## What's Next

- **Reflection events**: periodic `agent.reflected` events that compact episodic
  memories into high-level beliefs, shrinking the context window cost over long runs.
- **Illustrated serial**: a third scenario that introduces an image-generation tool
  via MCP and a wall-clock cadence (one episode per hour).
- **Persistent ledger**: swap the in-memory `Ledger` for a SQLite backend without
  changing a single scenario or agent.
- **Salience-scored retrieval**: replace the recency window with importance × recency
  scoring so agents surface the most meaningful memories, not just the most recent.

---

## The Stack

| Layer | Choice | Why |
|---|---|---|
| UI | Gradio | Required by hackathon; good enough for a toy |
| Event schema | Pydantic v2 | Strict validation, zero extra fields |
| Model | Any OpenAI-compatible API | `OPENAI_BASE_URL` lets you point at Ollama, Together, Groq, NVIDIA NIM |
| Memory | Ledger view (no separate store) | Consistency, simplicity, crash recovery |
| Orchestration | In-process, synchronous | Right size for a demo; async and durable execution available when needed |

---

*This blog is generated from `docs/journal/` as the build progresses.*
