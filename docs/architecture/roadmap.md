# Roadmap

> **Reprioritization:** a senior-architect review of the current tree —
> [next-steps/architecture-review-and-next-steps.md](next-steps/architecture-review-and-next-steps.md) —
> argues for a short correctness + observability sprint (real cross-agent
> visibility, tool/causality events) *before* resuming the reach-oriented phases
> below. To add a world, see [scenario-authoring.md](scenario-authoring.md).

Legend: ✅ done · ◐ foundations built, depth remaining · ○ planned

## Phase 0: Foundation ✅

Gradio shell, in-memory append-only ledger, deterministic tiny agents, docs spine
(vision, ADR-0001–0004, schema, runbooks), build-journal automation.

## Phase 1: Memory + Second Scenario ✅

EpisodicMemory, ContextBuilder, Governor (call caps), OpenAI-compatible provider,
Mystery Roots scenario, two-scenario Gradio UI, ADR-0005–0008.

## Phase 2: Reflection + Structured Output ✅

- `ReflectionTracker` wired into `ManifestAgent.act()` — emits `agent.reflected`.
- `agent.reflected` is a first-class kind; rendered as a belief in the projection.
- JSON constraint block in every agent prompt; `_raw_fallback` rate shown in stats.
- All shipped agents converted from `Agent` to manifest-driven config.
- `output_extra_fields` shapes per-scenario payloads.

## Phase 3: Persistence + Memory ◐

- ✅ `SQLiteLedger` (WAL, idempotent, `snapshot_to`, `from_file`, `tail`).
- ✅ `Conductor.restore()` + `snapshot_every`; `scripts/resume_run.py`.
- ○ Embedding-based relevance in `SalienceMemory` (still keyword overlap).
- ○ pgvector upgrade path for episodic retrieval at scale.

## Phase 4: Declarative Config + Tools ✅ (live MCP ○)

- ✅ YAML manifests + scenario configs + `models.yaml` under `config/`.
- ✅ `src/core/registry.py`: loader, cast resolution, handler binding.
- ✅ `WorldConfig` + `validate_world/agent/scenario` (UI/LLM-generatable, ADR-0011).
- ✅ Capability-checked `ToolRegistry` + `oracle` tool + `oracle-grove` scenario (ADR-0012).
- ✅ `tests/test_modularity.py`: new agent + scenario, zero engine edits.
- ○ Live MCP servers (image-gen, web-fetch) behind the same tool contract.

## Phase 5: Long-Running + Durable Execution ◐

- ✅ Token-aware Governor (`max_total_tokens`, `hourly_budget_usd`); per-agent
  token metering (ADR-0013).
- ✅ Two-clock `step(n_ticks=N)`; ledger-as-checkpoint resume.
- ◐ Serverless deploy: `modal_app.py` (scheduled run on a persistent volume).
- ○ Wall-clock cron + episode export (`scripts/export_episode.py`).
- ○ Temporal / Inngest durable wrapper; OpenTelemetry tracing; cost telemetry hook.

## Phase 6: Illustrated Serial (Third Scenario) ○

Draft→critique→revise creative loop on a wall-clock cadence; Artist agent backed by
an image-gen MCP tool; episode gallery.  Proves modularity across a third
structurally different scenario.  (`oracle-grove` already proves a tool-using cast.)

## Phase 7: Submission Package ○

UI polish, frozen demo seed + recorded run, social post, Codex judge rubric pass,
`_raw_fallback` < 10% with a live model, submit.

---

## What is built right now

The four stable contracts are realized **and exercised**: open event schema,
ledger API (memory + SQLite), declarative agent manifest, capability tool
contract.  Per-agent small-model routing, declarative validatable config, the
modularity-invariant test, and long-running foundations (resume, snapshot, token
budget, two-clock) are all in and green.

## Architecture stability guarantee

The four contracts are frozen; additions only:
1. **Event schema** (`src/core/events.py`) — new kinds are additive by construction.
2. **Ledger API** (`src/core/ledger.py`) — interface, not implementation.
3. **Agent manifest** (`src/core/manifest.py`) — backward-compatible additions only.
4. **Tool contract** (`src/tools/registry.py`) — capability contract, not implementation.

Everything else — scenarios, agents, models, UI, persistence backend, tools — is
hot-swappable via `config/`.
