# ADR-0010: Per-Agent Model Routing via Logical Profiles

## Status

Accepted

## Context

The manifest declared a `model_profile` (`tiny`/`fast`/`balanced`/`strong`) and
`resolve_model()` mapped a profile to a concrete model name.  But nothing wired
it through to inference: `ManifestAgent.act()` computed the model name and threw
it away, then called a single shared provider.  Every agent ran on one
`MODEL_NAME`.  The central pitch — *many small specialists, the right size for
each job* — was not actually happening.

## Decision

Introduce `ModelRouter` (`src/models/router.py`) as the one place per-agent model
selection occurs.  It maps each profile to a concrete provider with its own model
name, endpoint, and decoding config (temperature, max_tokens).  `ManifestAgent`
asks the router `for_profile(self.manifest.model_profile)` every turn and routes
inference there.

The router is config/env driven:
- `config/models.yaml` binds each profile to a concrete (small) model.
- `MODEL_TINY` / `MODEL_FAST` / `MODEL_BALANCED` / `MODEL_STRONG` override at runtime.
- With `offline=True` it serves a `DeterministicTinyModel` for *every* profile,
  so the test suite is reproducible with no inference.

> **Amended:** offline is no longer a *product* mode. The app requires live
> inference — `Registry.build_router()` raises when no backend is configured
> instead of serving the stub. The `offline=True` flag and `DeterministicTinyModel`
> are retained purely as the test/dev seam — the deterministic "mock data" the
> suite injects via `tests/conftest.py`.

Providers expose `last_usage`; the conductor meters those tokens into the
Governor (see ADR-0013).

## Consequences

- A scenario freely mixes tiers — e.g. a `tiny` Pocket Actor next to a `strong`
  Mystery Judge — which is the economic argument for many-small-over-one-big.
- Swapping a tier to a different small model is a one-line config change; no agent
  code names a model.
- The `tiny` profile (≤4B) gives a first-class Tiny Titan mode.
- The deterministic stub (the `offline=True` test seam) keeps the test suite green
  and network-free without it being a runtime product mode.
