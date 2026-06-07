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
(`modal/catalogue.py`):

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

`config/models.yaml` binds each profile to a model by its **catalogue key** — the
slug in `modal/catalogue.py`, the single source of truth for what is deployed. The
loader expands that key into the concrete binding, so the served id and endpoint
URL live in exactly one place (no parallel YAML to keep in sync):

```yaml
offline: null            # null=auto, true=stub everywhere, false=always live
profiles:
  tiny:
    endpoint: nemotron-3-nano-4b   # catalogue key (modal/catalogue.py)
    temperature: 0.7
    max_tokens: 160
  # fast / balanced / strong follow the same shape (see the file)
```

`Registry.from_dir()` resolves each `endpoint:` against the catalogue (via
`src/models/modal_catalogue.py`) and fills:

- `model`    = `openai/<served_model_id>`
- `base_url` = `https://${MODAL_WORKSPACE}--<app>-<endpoint>.modal.run/v1`
  (or `$MODAL_LLM_BASE_URL` if set; `""` when neither → offline stub)
- `api_key`  = `$MODAL_LLM_KEY` (a self-served vLLM endpoint accepts any token)

Only the workspace is deploy-specific, and it is never hard-coded. Adding/retuning
a model is a one-line edit in `modal/catalogue.py`; re-casting a tier is a one-line
`endpoint:` change here. Per-profile env overrides for the model string (highest
priority): `MODEL_TINY`, `MODEL_FAST`, `MODEL_BALANCED`, `MODEL_STRONG`. You can
also bind a profile explicitly with `model:` + `base_url:` instead of `endpoint:`
(an escape hatch for non-catalogue endpoints).

## Offline determinism

With no live binding configured — no `MODAL_WORKSPACE` and no `MODAL_LLM_BASE_URL`
— the router serves a `DeterministicTinyModel` for every profile (variant tagged
per profile). Demos and the entire test suite run with zero inference and full
reproducibility — the offline/online decision is made once in
`has_live_credentials()`, and `litellm` is imported lazily so it need not be
installed at all offline. There is no generic cloud key: live inference is always
against the small models you deploy on Modal.

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
- `src/models/modal_catalogue.py` — engine view of the catalogue (key → binding)
- `src/core/manifest.py` — `resolve_model()` (env → catalogue default)
- `src/core/registry.py` — `build_router()`, `_resolve_model_endpoints()`, `_expand_env()`
- `src/models/provider.py` — `ModelProvider.last_usage`, `estimate_tokens()`
- `src/models/openai_compat.py` — `has_live_credentials()`, role→system personas
- `modal/catalogue.py` — the single source of truth: every served model + provider app
