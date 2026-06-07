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
tokens — and, on the live path, real cost — into the Governor.

## Transport: the LiteLLM gateway (live path)

The router resolves *which* model; the provider is *how* it is called. On the
live path that transport is the **LiteLLM gateway** (`LiteLLMProvider`, ADR-0015):
a single idiomatic `litellm.completion(...)` call routes every profile, including
self-served OpenAI-compatible endpoints. The routing abstraction is unchanged —
only the transport moved off a hand-rolled SDK call.

```
ModelRouter._build(profile)
  offline → DeterministicTinyModel(variant="stub:<profile>")
  live    → LiteLLMProvider(model="openai/<hf id>", api_base=<modal url>, …)
```

Profiles map to the OpenAI-compatible vLLM endpoints served on Modal
(`modal/registry.py`):

| Profile | Modal endpoint | Served model id |
|---|---|---|
| `tiny` | `nemotron-3-nano-4b` | `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` |
| `fast` | `minicpm-4-1-8b` | `openbmb/MiniCPM4.1-8B` |
| `balanced` | `gemma-4-12b` | `google/gemma-4-12B` |
| `strong` | `gemma-4-26b` | `google/gemma-4-26B-A4B-it` |

The LiteLLM model string for an OpenAI-compatible custom endpoint is
`openai/<served_model_id>` with `api_base` pointing at the endpoint's `/v1` URL.

### Real cost → Governor

LiteLLM prices each call (`response._hidden_params["response_cost"]`, falling back
to `litellm.completion_cost(response)` — both guarded, so an unpriced self-served
model yields `0.0`). The provider exposes it on `last_usage["cost_usd"]` (and
`last_cost`); `ManifestAgent` carries it, and the conductor passes it to
`governor.record_call(tokens=…, cost_usd=…)`. This makes `hourly_budget_usd` a real
spend cap on the live path. Offline cost is always `0.0`.

## Configuration

`config/models.yaml` binds each profile to a concrete model + endpoint + decoding.
Only the Modal workspace is deploy-specific, so it is templated from
`$MODAL_WORKSPACE` and never hard-coded; `$MODAL_LLM_KEY` is the endpoint key:

```yaml
offline: null            # null=auto, true=stub everywhere, false=always live
profiles:
  tiny:
    model: openai/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16
    base_url: https://${MODAL_WORKSPACE}--nvidia-llms-nemotron-3-nano-4b.modal.run/v1
    api_key: ${MODAL_LLM_KEY}
    temperature: 0.7
    max_tokens: 160
  # fast / balanced / strong follow the same shape (see the file)
```

`Registry.from_dir()` expands `${VAR}` references when it loads the file. If any
referenced var is unset, that string collapses to `""` (a binding built from a
missing workspace is *not configured*, not a half-templated URL) and the validator
nulls it. Runtime env overrides for the model name (highest priority): `MODEL_TINY`,
`MODEL_FAST`, `MODEL_BALANCED`, `MODEL_STRONG`, then `MODEL_NAME` (these feed the
`from_env` default path; explicit `models.yaml` specs win on the registry path).

## Offline determinism

With no live binding configured — neither `OPENAI_API_KEY` nor
`MODAL_WORKSPACE`/`MODAL_LLM_BASE_URL` — the router serves a
`DeterministicTinyModel` for every profile (variant tagged per profile). Demos and
the entire test suite run with zero inference and full reproducibility — the
offline/online decision is made once in `has_live_credentials()`, and `litellm` is
imported lazily so it need not be installed at all offline.

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
- `src/models/litellm_provider.py` — `LiteLLMProvider` (live transport, real cost)
- `src/core/manifest.py` — `resolve_model()` (env → default name resolution)
- `src/core/registry.py` — `build_router()`, `_expand_env()` (YAML env templating)
- `src/models/provider.py` — `ModelProvider.last_usage`, `estimate_tokens()`
- `src/models/openai_compat.py` — `has_live_credentials()`, role→system personas
- `modal/registry.py` — the served endpoints each profile points at
