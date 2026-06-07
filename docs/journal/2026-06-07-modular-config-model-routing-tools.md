# Modular Config, Per-Agent Model Routing, Tools, Long-Running

Date: 2026-06-07

## Built

- **Opened the event schema** (ADR-0009). `kind` is now a format-validated,
  dot-namespaced string, not a closed `Literal`. Scenarios mint their own kinds
  (`clue.found`, `oracle.spoke`) with zero engine edits; authority lives in
  `manifest.may_emit`. A generic projection fallback renders any text-bearing
  custom kind on stage.
- **Per-agent model routing** (ADR-0010). New `ModelRouter` maps each agent's
  logical profile (`tiny`/`fast`/`balanced`/`strong`) to a concrete small model
  with its own decoding config. Fixed the dead path where `resolve_model()` was
  computed and discarded. Offline it serves a deterministic stub per profile.
- **Activated `ManifestAgent`** for real: salience memory is now actually used,
  reflection emits `agent.reflected` at threshold, model is routed by profile,
  tokens are metered. Both shipped scenarios are now manifest-driven.
- **Declarative, validatable config** (ADR-0011). `config/` holds YAML for agents,
  scenarios, and model profiles. `src/core/config.py` + `registry.py` load and
  validate them; `validate_world/agent/scenario` make the surface UI/LLM-generatable.
- **Capability-checked tool contract** (ADR-0012). `ToolRegistry` enforces
  `manifest.tools`; a deterministic `oracle` tool + a `fortune-teller` handler in a
  new `oracle-grove` scenario exercise the path end-to-end. MCP servers deferred.
- **Long-running foundations** (ADR-0013). Token/spend-aware Governor;
  `step(n_ticks=N)` two-clock; `restore()` + `snapshot_every`; `scripts/resume_run.py`
  and an optional `modal_app.py` serverless deployment.
- **UI**: scenarios load via the registry; a live "config-as-data" panel shows the
  cast, model tiers, tool grants, and goal. Token/cost and structured-output health
  in stats.
- **Tests**: 128 → 185, still zero mocks. New: events, router, manifest-agent,
  config, registry, modularity invariant, tools, long-running, governor budgets.

## Decisions

- The four stable contracts are now realized *and exercised*, not aspirational.
  Config is data; behaviour is the only thing that stays in Python (handlers).
- Shipped scenarios became thin `build_scenario()` shims over the registry, so the
  same path the demo uses is the path a drop-in world uses.
- `max_consecutive` enforcement deferred: a `tick_every=1` driver legitimately acts
  every turn, so a blanket cap would break cadence. Documented, not half-built.

## Learned

- The modularity machinery had been built in Phase 1–2 but left dead-wired (model
  profile discarded, salience computed then dropped, reflection never triggered).
  Activating it surfaced and fixed three latent bugs.
- A closed event-kind `Literal` was a hidden modularity ceiling — and the memory
  importance table already referenced kinds it would have rejected.

## Next

- Live MCP servers behind the existing tool contract (image-gen first).
- Embedding-based relevance in `SalienceMemory` (currently keyword overlap).
- Wall-clock cron + episode export; cost telemetry into `record_call(cost_usd=…)`.
- The illustrated-serial scenario (Phase 6) to prove modularity across a third shape.
