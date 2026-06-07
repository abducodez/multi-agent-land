# Roadmap

## Phase 0: Foundation ✅

- Gradio app shell
- In-memory append-only event ledger
- Three deterministic tiny agents (SceneWhisperer, MischiefCritic, PocketActor)
- Documentation spine: vision, ADR-0001–0004, schema docs, runbooks
- Build journal automation

## Phase 1: Memory + Second Scenario ✅

- EpisodicMemory: per-agent filtered view over the ledger
- ContextBuilder: single-point prompt assembly
- Governor: budget guard (turns, per-turn calls, total calls)
- OpenAI-compatible model provider (any OpenAI-compatible endpoint)
- EchoAgent: transforms visitor injections
- Mystery Roots scenario: blackboard swarm, zero engine edits
- Enhanced Gradio UI: two-scenario dropdown, seed gallery, governor stats
- ADR-0005–0008; engine architecture blog post
- Tests: 14 → 70 passing

## Phase 2: Reflection + Structured Output

- Wire `ReflectionTracker` into `ManifestAgent.act()` — emit `agent.reflected`
- Add `agent.reflected` to EventKind; render beliefs in stage projection
- JSON constraint block in every agent prompt; track `_raw_fallback` rate
- Convert Tiny Wood agents from `Agent` to `ManifestAgent`
- Add `output_extra_fields` to manifest for per-scenario payload shaping
- Agent eval harness: measure character consistency and raw-fallback rate
- Full details: `docs/architecture/next-steps/phase-2-reflection-structured.md`

## Phase 3: Persistence + Embedding Memory

- Wire `SQLiteLedger` into the Gradio app (env var: `DB_PATH`)
- `Conductor.restore()` for crash recovery demo
- Periodic snapshots (`snapshot_every` param)
- Embedding-based relevance in `SalienceMemory` (sentence-transformers or API)
- `scripts/resume_run.py` crash-recovery entry point
- Optional: `[embed]` dependency group; pgvector upgrade path documented
- Full details: `docs/architecture/next-steps/phase-3-persistence-recovery.md`

## Phase 4: Manifest Discovery + MCP Tool Integration

- YAML manifest files in `agents/<name>/manifest.yaml`
- `src/core/registry.py`: manifest loader + handler discovery
- Scenario config YAML replaces hardcoded agent lists
- MCP client in `src/tools/mcp_client.py`
- First MCP tool: image-gen server in `tools/image-gen/server.py`
- Capability-based access control in tool registry
- `tests/test_modularity.py`: invariant proof test
- Full details: `docs/architecture/next-steps/phase-4-manifest-discovery-mcp.md`

## Phase 5: Long-Running + Durable Execution

- Two-clock model: wall-clock cadence + sim-time ticks
- `scripts/cron_episode.py`: hourly episode trigger
- `scripts/export_episode.py`: episode artifact export (Markdown/JSON)
- Governor: hourly budget + cost tracking via LLM observability
- Optional: Modal deployment (`modal_app.py`) for serverless long runs
- Optional: Temporal workflow for maximum crash-reliability
- OpenTelemetry tracing: end-to-end turn visibility
- Full details: `docs/architecture/next-steps/phase-5-long-running-durable.md`

## Phase 6: Illustrated Serial (Third Scenario)

- New scenario: draft→critique→revise creative loop, wall-clock cadence
- Agent cast: Beat Proposer, Dialogue Writer, Scene Describer, Artist (image-gen),
  Continuity Keeper, Serial Judge, Episode Publisher
- New event kinds: `beat.proposed`, `image.generated`, `episode.started`, `episode.published`
- Gradio: episode gallery, current draft panel, arc status
- Proves modularity holds across 3 structurally different scenarios
- Full details: `docs/architecture/next-steps/phase-6-illustrated-serial.md`

## Phase 7: Submission Package

- Polish Gradio UI: animations, custom font, stage transitions
- Freeze demo seed: record a canonical 15-turn run that shows all mechanics
- Record demo video: 90-second screencap with narration
- Write social post: hackathon hook, one striking generated moment, repo link
- Run the Codex judge rubric from `docs/strategy/codex-judge-rubric.md`
- Final `_raw_fallback` rate check: must be <10% with the live model
- Submit

---

## Architecture stability guarantee

The four stable contracts are frozen after Phase 2:
1. **Event schema** (`src/core/events.py`) — new kinds additive only
2. **Ledger API** (`src/core/ledger.py`) — interface, not implementation
3. **Agent manifest** (`src/core/manifest.py`) — backward-compatible additions only
4. **Tool/MCP contract** (`src/tools/`) — capability contract, not implementation

Everything else is hot-swappable.  Scenarios, agents, models, UI, and the
persistence backend can all change without breaking the contracts.
