# Multi-Agent Land

Hackathon project for the **Thousand Token Wood** trail: a small-model, multi-agent
interactive story engine where the AI is load-bearing for the experience.

> One tiny event-sourced engine can power many delightful worlds.  The first world is
> a whimsical forest theater where small specialist agents write, judge, remember, and
> render strange interactive scenes.  The second is a mystery-solving blackboard swarm.
> Both run on the same four abstractions.

---

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Optional: add your API key for live inference
cp .env.example .env              # then fill in OPENAI_API_KEY

python app.py
```

The app runs on a **deterministic local stub** with no API key — great for testing
and demos that need to be fully reproducible.  Add an `OPENAI_API_KEY` to switch to
live inference.  Any OpenAI-compatible endpoint works (Together AI, Groq, Ollama,
NVIDIA NIM) — set `OPENAI_BASE_URL` in `.env`.

### Run tests

```bash
python -m pytest tests/ -v
```

---

## What It Is

A **tiny theater engine** powered by specialist small-model agents.  Agents never
call each other directly — they post typed events to a shared append-only ledger,
and every view (the stage, the memory, the UI) is a projection derived from that log.

The user can:
- **Start** a run from a seed — any weird premise works.
- **Advance** one turn and watch the agents react.
- **Drop** a disturbance into the world — the agents absorb and transform it.
- **Switch** between scenarios without reloading.

### Scenarios

| Name | Cognitive task | Agents |
|---|---|---|
| 🍄 Thousand Token Wood | Divergent world-growth | Seedkeeper, Critic, Pocket Actor, Echo |
| 🔍 Mystery Roots | Convergent mystery-solving | Clue Gatherer, Hypothesis Former, Devil's Advocate, Judge |

Adding a third scenario requires one new file and one two-line registry entry.
**Zero engine edits.**

---

## Architecture in 90 seconds

```
Visitor seed or disturbance
         │
    Conductor ← Governor (budget guard)
         │
    schedule(turn) → [Agent₁, Agent₂, ...]
         │
    ContextBuilder
         ├── Pinned persona
         ├── Current scene  (projection)
         ├── Episodic memory (ledger view)
         └── Visitor disturbances
         │
    ModelProvider.complete(role, prompt)
         │
    Typed Event → Ledger.append()
         │
    Projections update
         │
    Gradio UI renders stage + ledger + stats
```

### Key decisions (see `docs/adr/` for full reasoning)

| # | Decision |
|---|---|
| 0001 | Append-only event ledger as the sole source of truth |
| 0002 | Gradio as the UI layer |
| 0003 | Small specialist agents over one large model |
| 0004 | Document every architectural decision as we build |
| 0005 | Agent memory is a ledger view, not a separate store |
| 0006 | `ContextBuilder` owns prompt assembly; agents own only persona + action |
| 0007 | `Governor` is injected into the conductor to enforce call budgets |
| 0008 | Zero engine edits required to add a second scenario |

---

## Repository map

```
app.py                      Gradio composition root
src/
  core/
    events.py               Event schema (Pydantic, strict)
    ledger.py               Append-only in-memory ledger
    projections.py          Pure-function stage projection
    conductor.py            Turn scheduler + reset + inject
    memory.py               EpisodicMemory — per-agent ledger view
    context.py              ContextBuilder — prompt assembly
    governor.py             Budget guard (turns, calls per turn, total calls)
  agents/
    base.py                 Abstract Agent protocol
    tiny_wood.py            Thousand Token Wood cast
  scenarios/
    base.py                 Scenario dataclass + default schedule
    thousand_token_wood.py  First scenario config
    mystery_roots.py        Second scenario config — modularity proof
  models/
    provider.py             ModelProvider ABC + DeterministicTinyModel stub
    openai_compat.py        OpenAI-compatible provider + env-aware factory
  ui/
    render.py               Gradio rendering helpers
tests/                      70 passing tests, zero mocks
docs/
  vision.md                 One-page product and technical vision
  architecture/             System design and turn lifecycle
  adr/                      Append-only Architecture Decision Records (0001–0008)
  schema/                   Event and manifest contracts
  runbooks/                 Local dev, demo, recovery
  strategy/                 Hackathon prize strategy
  blog/                     Technical blog posts built along the way
  journal/                  Daily build log entries
scripts/
  new_journal_entry.py      Creates dated build log entries
  snapshot_progress.py      Updates the living blog from journal
```

---

## Hackathon targets

- **Genuinely delightful** — strange, joyful, worth showing a friend
- **AI is load-bearing** — agents generate the evolving scene; the user does not author it
- **Small models** — every runtime model ≤ 32B, with an optional ≤ 4B Tiny Titan mode
- **Polished Gradio** — custom theme, live ledger, visible agent trace, demo-ready seeds
- **Prize stacking** — Thousand Token Wood, Community Choice, OpenAI Track, Tiny Titan,
  Best Agent, Off-Brand UI, Best Demo, Judges' Wildcard

---

## Development loop

```bash
# 1. Build the thinnest slice
# 2. Record the decision
python -c "from scripts.new_journal_entry import main; main()" "What changed today"
# 3. Regenerate the living blog
python scripts/snapshot_progress.py
# 4. Confirm nothing broke
python -m pytest tests/ -q
```
