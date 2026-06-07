# Model Routing

Per-agent model selection is the heart of the "small models, easily configurable"
story.  Each agent declares a **logical profile**; the `ModelRouter` resolves it
to a **concrete small model** with its own decoding config.  No agent code ever
names a model.

## The profiles

| Profile | Param target | Role of thumb |
|---|---|---|
| `tiny` | ≤4B | cheap, high-volume workers (Tiny Titan mode) |
| `fast` | ≤7B | default workers |
| `balanced` | ≤13B | local judges, salience-heavy roles |
| `strong` | ≤32B | the global judge, reflection passes |

## How a turn resolves a model

```
manifest.model_profile  ──►  ModelRouter.for_profile(profile)  ──►  ModelProvider
        (e.g. "tiny")              (cached per profile)            (concrete model)
```

`ManifestAgent._complete()` calls `router.for_profile(self.manifest.model_profile)`
every turn and records the provider's `last_usage` so the conductor can meter
tokens into the Governor.

## Configuration

`config/models.yaml` binds each profile to a concrete model + decoding:

```yaml
offline: null            # null=auto, true=stub everywhere, false=always live
profiles:
  tiny:     { model: qwen2.5-3b-instruct,  temperature: 0.7, max_tokens: 160 }
  fast:     { model: qwen2.5-7b-instruct,  temperature: 0.9, max_tokens: 220 }
  balanced: { model: qwen2.5-14b-instruct, temperature: 0.8, max_tokens: 320 }
  strong:   { model: qwen2.5-32b-instruct, temperature: 0.6, max_tokens: 480 }
```

Runtime env overrides (highest priority): `MODEL_TINY`, `MODEL_FAST`,
`MODEL_BALANCED`, `MODEL_STRONG`, then `MODEL_NAME` as a final fallback.

## Offline determinism

With no `OPENAI_API_KEY`, the router serves a `DeterministicTinyModel` for every
profile (variant tagged per profile).  Demos and the entire test suite run with
zero inference and full reproducibility — the offline/online decision is made
once in `has_live_credentials()`.

## Mixing tiers in one cast

This is the economic payoff.  Mystery Roots runs three cheap workers and one
strong verifier:

```
clue-gatherer    fast       hypothesis-former  balanced
devils-advocate  fast       mystery-judge      strong
```

Many weak proposers, one strong judge — at a fraction of the cost of running
everything on the big model.

## Code

- `src/models/router.py` — `ModelRouter`, `ProfileSpec`, `_PROFILE_DECODING`
- `src/core/manifest.py` — `resolve_model()` (env → default name resolution)
- `src/models/provider.py` — `ModelProvider.last_usage`, `estimate_tokens()`
- `src/models/openai_compat.py` — live provider, real usage capture
