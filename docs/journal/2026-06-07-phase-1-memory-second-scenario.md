# 2026-06-07 — Phase 1: Memory, Governor, Second Scenario

## What changed

Phase 0 had a working skeleton: ledger, conductor, three deterministic stub agents, and
a Gradio UI.  Phase 1 adds the cognitive infrastructure that makes agents feel like
inhabitants rather than random sentence generators.

### New engine modules

- **`src/core/memory.py`** — `EpisodicMemory`: per-agent filtered view over the ledger.
  Agents see their own actions plus globally-visible events.  Capped at 8 events to
  fit small-model windows.

- **`src/core/context.py`** — `ContextBuilder`: one place where persona, world state,
  memory, and visitor disturbances are assembled into a prompt string.  Changing prompt
  structure is now a one-file edit.

- **`src/core/governor.py`** — `Governor`: enforces turn, per-turn call, and total call
  budgets.  `BudgetExceeded` is a named exception.  Injected into the conductor.

### New model provider

- **`src/models/openai_compat.py`** — `OpenAICompatProvider`: works with any
  OpenAI-compatible API.  Config via `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `MODEL_NAME`.
  Falls back to `DeterministicTinyModel` when no key is present.

### Enhanced agents

All four Tiny Wood agents now use `ContextBuilder` with rich personas and the full
episodic memory window.  The `EchoAgent` is new — it transforms visitor injections
through the wood's logic.

### Second scenario: Mystery Roots

`src/scenarios/mystery_roots.py` demonstrates the modularity claim: same engine,
zero engine edits, different cognitive task.  Four agents (ClueGatherer,
HypothesisFormer, DevilsAdvocate, MysteryJudge) work a 4-phase convergence cycle.

### Tests

Grew from 14 to 70 passing tests.  New suites: `test_memory`, `test_governor`,
`test_mystery_roots`, `test_events`, `test_projections`, `test_scenario`.

### UI

- Two-scenario dropdown with seed gallery per scenario
- Governor stats in the run-stats panel
- Richer custom CSS with CSS variables, stage gradient, monospace ledger

## Key decisions

- Memory is a ledger view, not a separate store (ADR-0005)
- Prompt assembly lives in `ContextBuilder`, not agent code (ADR-0006)
- Governor is injected into conductor, not embedded in agents (ADR-0007)
- Zero engine edits for second scenario — modularity claim verified (ADR-0008)

## Next

- Reflection events (compact old memories into beliefs)
- Persistent SQLite ledger backend
- Illustrated serial scenario (image-gen via MCP)
- Demo-mode auto-run with gallery of frozen interesting seeds
