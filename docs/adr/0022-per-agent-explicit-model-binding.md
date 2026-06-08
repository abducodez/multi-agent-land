# ADR-0022: Per-Agent Explicit Model Binding (`model_endpoint`)

## Status

Accepted

## Context

ADR-0010 gave per-agent model selection through four **logical tiers**
(`tiny`/`fast`/`balanced`/`strong`), and `config/models.yaml` binds each tier to one
concrete catalogue model. That is the right default, but it has two limits:

1. **A cast can express at most four distinct models** — one per tier. Two agents on
   the same tier always share a model.
2. **The unbound specialist models are unreachable.** Several catalogue entries have
   `profile=None` (Nemotron Cascade 14B, Nemotron 30B, MiniCPM-o) precisely so they do
   *not* displace a tier default — but then no manifest could ever cast them.

Both bite the hackathon strategy directly: the unfair advantage is running *different
sponsor models in one cast* (Judge → Nemotron, a worker → MiniCPM) to qualify for
multiple tracks from a single submission. And the Fishbowl Lab needed to let a user pick
concrete Modal-hosted models per cast member — with the pick actually driving the run
(the Lab's model controls were previously cosmetic: `on_summon` ignored
`collect_world_config` and always built the scenario's default cast).

## Decision

Add an optional, additive per-agent override that names a **specific catalogue model**,
leaving the tier system as the default and fallback.

- **Manifest.** `AgentManifest` gains `model_endpoint: str | None = None` — a
  `modal/catalogue.py` endpoint slug. `None` → route by `model_profile` (unchanged).
- **Routing.** `ManifestAgent` routes by a **route key** —
  `self._route_key = model_endpoint or model_profile` — and calls
  `router.for_profile(self._route_key)`. The `ModelRouter` already accepts any key;
  `_spec_for` now resolves a non-tier key against the catalogue (`_catalogue_spec`:
  `modal_catalogue.binding_for(key)` for the live model string / endpoint URL / api key,
  with decoding inherited from the model's tier — an unbound specialist → `balanced`).
  An unknown non-tier key degrades to the `fast` tier rather than crashing. Offline this
  path is never reached: `_build` serves the deterministic stub for any key, with the key
  folded into the stub's `variant` so a different pick still varies (reproducible) output.
- **Composed runs.** `Registry.from_world(world)` builds an in-memory registry from a
  validated `WorldConfig`, so a UI- (or LLM-) composed run flows through the same
  `build_scenario` / `build_router` / `governor_for` path as a config-file run.
- **Fishbowl Lab.** The cast section is a `@gr.render` over the scenario: one model
  `gr.Dropdown` per non-judge player (the Judge picks in §04), its choices sourced *only*
  from `modal_catalogue.entries()`. Picks accumulate in a `cast_models` state;
  `collect_world_config` maps each onto the agent's `model_endpoint` (re-checking the key
  against the catalogue), and `Summon` runs the composed world. Only catalogue-hosted
  models are offerable, and the selection is load-bearing.

## Consequences

- A cast can pin **any** catalogue model per agent, including the unbound specialists —
  enabling genuine multi-sponsor-model casts from one engine, one submission.
- The tier abstraction (ADR-0010) is untouched: it remains the default, the decoding
  source, the offline-variant tag, and the fallback. `model_endpoint` is purely additive,
  so every existing manifest, scenario, and test is byte-identical (defaults to `None`).
- The Lab's model picker is now functional, not cosmetic: the model you choose is the
  model that speaks (offline → the deterministic stub, demo still reproducible). A bad
  compose degrades to the scenario's default cast, so Summon never breaks the demo.
- A run cannot point at an undeployed model: the UI offers only catalogue entries, and
  `collect_world_config` re-validates the key, dropping anything out-of-band or stale.
- Offline determinism is preserved end-to-end (the route key, not just the tier, seeds
  the stub).

## Alternatives considered

- **Per-tier rebinding** (let the run choose which catalogue model backs each of the four
  tiers): zero engine change, but still capped at four distinct models and still cannot
  reach the unbound specialists. Rejected as too weak for the multi-sponsor goal.
- **Widening `ModelProfile` to an arbitrary `str`**: would dissolve the tier contract that
  drives decoding defaults, the `MODEL_<TIER>` env overrides, and the offline variant.
  Rejected in favour of a separate, additive field that keeps both concepts crisp.

## Code

- `src/core/manifest.py` — `AgentManifest.model_endpoint`
- `src/agents/base.py` — `ManifestAgent._route_key`
- `src/models/router.py` — `ModelRouter._spec_for` / `_catalogue_spec`
- `src/core/registry.py` — `Registry.from_world`
- `src/ui/fishbowl/lab.py` — `model_choices`, `_cast_defaults`, `_judge_manifest`, the
  cast `gr.render`, `collect_world_config`
- `src/ui/fishbowl/app.py` — `_compose_session`, the `Summon` wiring

See also: ADR-0010 (logical-profile routing), ADR-0011 (declarative validatable config),
ADR-0015 (LiteLLM gateway to Modal models), ADR-0019 (single model catalogue), ADR-0021
(Fishbowl Gradio presenter).
