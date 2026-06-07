# Multi-Agent Land

Hackathon project for the **Thousand Token Wood** trail: a small-model, multi-agent
interactive story engine where the AI is load-bearing for the experience.

> One tiny event-sourced engine can power many delightful worlds.  A whimsical forest
> theater, a mystery-solving blackboard swarm, and a tool-using oracle grove are not
> three apps — they are three YAML configs of the *same* engine.  Small specialist
> agents write, judge, remember, and render strange interactive scenes, each on the
> small model that fits its job.

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

What makes it *super modular*:
- **Config, not code.** Agents, scenarios, casts, model tiers, tool grants, and
  budgets are declarative YAML under `config/`, validated by a schema. Add a world
  by adding files — proven by `tests/test_modularity.py` (zero engine edits).
- **A model per agent.** Each agent declares a logical profile (`tiny`/`fast`/
  `balanced`/`strong`); a `ModelRouter` binds it to a concrete small model. Mix a
  ≤4B worker with a ≤32B judge in one cast.
- **Capability-checked tools.** Agents call tools only if their manifest grants
  them — the contract that fronts in-process tools today and MCP servers later.
- **Built to run for hours.** The ledger is the checkpoint: `restore()` resumes a
  killed run; a token-aware governor bounds spend; `step(n_ticks=N)` maps one
  wall-clock episode onto N sim-ticks.

The user can **Start** from any seed, **Advance** a turn, **Drop** a disturbance,
and **Switch** scenarios — all live.

### Scenarios (each is a YAML config)

| Name | Cognitive task | Cast (model tiers) |
|---|---|---|
| 🍄 Thousand Token Wood | Divergent world-growth | Seedkeeper `fast`, Critic `balanced`, Pocket Actor `tiny`, Echo `fast` |
| 🔍 Mystery Roots | Convergent mystery-solving | Clue Gatherer `fast`, Hypothesis Former `balanced`, Devil's Advocate `fast`, Judge `strong` |
| 🔮 Oracle Grove | Tool-using prophecy | Seedkeeper `fast`, Fortune-Teller `fast` + `oracle` tool |

Adding a fourth scenario is a new YAML file in `config/scenarios/`. **Zero engine edits.**

---

## Architecture in 90 seconds

```
config/ (YAML) → Registry → Scenario(cast) + ModelRouter + ToolRegistry
         │
Visitor seed or disturbance
         │
    Conductor ← Governor (calls + tokens + spend)
         │
    subscription queue + tick schedule → [Agent₁, Agent₂, ...]
         │
    ContextBuilder        ModelRouter.for_profile(tiny|fast|balanced|strong)
         ├ persona             │  → the right small model per agent
         ├ shared goal         ▼
         ├ scene (projection)  inference → structured JSON event
         ├ memory (ledger view, windowed or salience-ranked)
         └ visitor + granted tools
         │
    Typed Event → Ledger.append()  (idempotent; SQLite-backed for long runs)
         │
    Projections update → Observer (read-only) → Gradio UI (stage + ledger + stats + live config)
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
| 0009 | Event kinds are open + format-validated; authority lives in `may_emit` |
| 0010 | Per-agent model routing via logical profiles (`ModelRouter`) |
| 0011 | Declarative, validatable config — UI/LLM-generatable (`WorldConfig`) |
| 0012 | Capability-based tool contract (`ToolRegistry`); MCP-ready |
| 0013 | Token-aware governor + long-running foundations (restore/snapshot/two-clock) |

---

## Add a world without code

Drop two YAML files into `config/` and it appears in the app — no engine edit.

```yaml
# config/agents/town-crier.yaml
name: town-crier
persona: You are the Town Crier. Announce one bit of news in a sentence.
may_emit: [crier.announced]        # a brand-new namespaced kind, minted by config
schedule: { tick_every: 1 }
model_profile: tiny                # routed to a ≤4B model
```

```yaml
# config/scenarios/town-square.yaml
name: town-square
title: "📣 Town Square"
goal: Keep the square informed.
default_seed: Market day in a town that forgets its own name nightly.
cast: [town-crier]                 # who participates
```

A UI form or an LLM can emit the same structure and validate it before running:
`validate_world({...})` raises if a cast names an undefined agent. The invariant is
enforced by a test (`tests/test_modularity.py`). See
[docs/architecture/config-system.md](docs/architecture/config-system.md).

## Repository map

```
app.py                      Gradio composition root (loads scenarios from config/)
config/                     THE configurable surface (declarative, validatable)
  models.yaml               Logical profile → concrete small model
  agents/*.yaml             One AgentManifest per agent
  scenarios/*.yaml          One ScenarioConfig per scenario (cast = agent names)
src/
  core/
    events.py               Event schema — open, namespaced, validated kinds
    ledger.py               Append-only in-memory ledger
    sqlite_ledger.py        Persistent ledger (WAL, snapshot, restore, tail)
    projections.py          Pure-function stage projection (+ generic kind fallback)
    conductor.py            Two-clock loop, subscription+tick routing, restore/snapshot
    memory.py               Episodic / salience / reflection — all ledger views
    context.py              ContextBuilder — layered prompt assembly
    governor.py             Budget guard (calls + tokens + spend)
    manifest.py             AgentManifest — the agent contract + resolve_model
    config.py               ScenarioConfig / ModelsConfig / WorldConfig + validators
    registry.py             Loads config/, resolves casts, binds handlers
    structured.py           JSON output instruction + tolerant parser
    observer.py             Read-only renderer with view diffs
  agents/
    base.py                 Agent ABC + ManifestAgent (the workhorse)
    handlers.py             Behaviour handlers (e.g. FortuneTeller — calls a tool)
  scenarios/
    base.py                 Scenario dataclass (goal, genesis, legacy schedule)
    thousand_token_wood.py  Thin build_scenario() → registry
    mystery_roots.py        Thin build_scenario() → registry
  models/
    provider.py             ModelProvider ABC + DeterministicTinyModel + usage
    openai_compat.py        OpenAI-compatible provider + credentials check
    router.py               ModelRouter — per-agent profile → small model
  tools/
    registry.py             ToolRegistry — capability-checked broker
    builtins.py             oracle tool + default_tool_registry()
  ui/
    render.py               Gradio rendering helpers + live config panel
tests/                      185 passing tests, zero mocks
docs/
  vision.md                 One-page product and technical vision
  architecture/             Overview, model-routing, config-system, tool-contract, long-running, …
  adr/                      Architecture Decision Records (0001–0013)
  schema/                   events / agent-manifest / scenario-config / world-config
  runbooks/ strategy/ blog/ journal/
scripts/
  resume_run.py             Resume a long-running scenario from a SQLite ledger
  new_journal_entry.py      Creates dated build log entries
  snapshot_progress.py      Updates docs/blog/building-in-public.md from journal
modal/
  service.py                Reusable vLLM serving layer (OpenAI-compatible)
  registry.py               Declarative model catalogue, grouped by provider
  app_*.py                  One Modal app per provider (nvidia/openbmb/google)
  snapshot_progress.py      Updates the living blog from journal
modal_app.py                Optional: serverless scheduled run (Modal)
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
