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
manifest.model_endpoint or model_profile  ──►  ModelRouter.for_profile(key)  ──►  ModelProvider
        (the agent's "route key")                 (cached per key)              (concrete model)
```

`ManifestAgent` computes a **route key** — `self._route_key`, the explicit
`model_endpoint` when set, else the `model_profile` tier — and calls
`router.for_profile(self._route_key)` every turn, recording the provider's
`last_usage` so the conductor can meter tokens (and, live, real cost) into the
Governor.  The router accepts either kind of key: a tier resolves to the profile
default, a catalogue endpoint slug to that specific model's binding.

## Pinning a specific model (`model_endpoint`)

Tiers are the default, but a manifest can pin one mind to a **specific catalogue
model** by setting `model_endpoint` to an endpoint slug from `modal/catalogue.py`
(e.g. `minicpm-4-1-8b`).  This overrides the tier and is how a cast mixes concrete
sponsor models — one worker on MiniCPM, the Judge on Nemotron Cascade — including the
*unbound specialist* models that no tier defaults to.  See ADR-0022.

```
ModelRouter._spec_for(key)
  key in specs                → that ProfileSpec (the four tiers from models.yaml)
  key is a catalogue endpoint → _catalogue_spec(key): binding_for(key) + the model's
                                tier decoding (unbound specialist → balanced defaults)
  unknown non-tier key        → degrade to the fast tier (never crash)
```

Offline this path is never reached — `_build` serves the deterministic stub for any
key, with the key folded into the stub's `variant`, so picking a different model still
varies the (reproducible) output.  The **Fishbowl Lab** writes `model_endpoint` from
its per-cast model picker, so the model you choose in the UI is the model that runs
(see [fishbowl-ui.md](fishbowl-ui.md)).

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

### Backends: Modal · Hugging Face · Local GPU

A *backend* is just a catalogue + a binding rule, unified behind one registry
(`src/models/inference.py`, ADR-0024). Models are named by a **backend-qualified
key** `"<backend>:<raw>"`; a bare key means Modal, so all existing config keeps
working. Three backends ship today:

| Backend | Prefix | Where it runs | Opt-in |
|---|---|---|---|
| Modal | *(bare)* | vLLM endpoints you deploy on Modal GPUs | `MODAL_WORKSPACE` / `MODAL_LLM_BASE_URL` |
| Hugging Face | `hf:` | serverless Inference Providers router | `HF_TOKEN` |
| Local GPU | `local:` | in-process `transformers` model on the host's own GPU | `SPACES_ZERO_GPU` / `LOCAL_INFERENCE=1` / CUDA auto-detected |

The local backend (ADR-0033, supersedes ADR-0032) runs a cast fully in-process —
no HTTP server, no extra process, no token. It loads a small instruct model via
`transformers` inside a `@spaces.GPU` function (`LocalTransformersProvider`,
`src/models/local_provider.py`). The hardware path is transparent:

- **ZeroGPU Space** — `@spaces.GPU` allocates a GPU per call from the shared pool
  (~5 min/day free quota). Enabled when `SPACES_ZERO_GPU` is set.
- **Dedicated-GPU Space** (T4 / L4 / L40S / A100) — persistent GPU, no per-call
  quota. Enabled with `LOCAL_INFERENCE=1`.
- **Local CUDA box** — the same `LOCAL_INFERENCE=1` flag, or CUDA auto-detected at
  startup.

Off ZeroGPU and without `LOCAL_INFERENCE=1`, `@spaces.GPU` is a no-op and the
engine falls back to the deterministic stub — so the demo is always reproducible on
CPU-only hosts. Pick "Local GPU" in the Lab's backend radio to opt in per run.

Available models (all ≤32B; select via `local:<repo_id>`). One sponsor family per tier, so
a single cast spans four sponsors at once (the multi-track strategy):

| Key | Model | Tier | Notes |
|---|---|---|---|
| `local:nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` | Nemotron Nano 4B | **tiny** | NVIDIA lane; Tiny-Titan ≤4B band; `trust_remote_code`; Mamba-2 hybrid (BF16, not GGUF) |
| `local:openbmb/MiniCPM4.1-8B` | MiniCPM 4.1 8B | fast | OpenBMB lane; `trust_remote_code` |
| `local:CohereLabs/aya-expanse-8b` | Aya Expanse 8B | balanced | Cohere lane; **gated repo** — needs licence acceptance + `HF_TOKEN` |
| `local:JetBrains/Mellum2-12B-A2.5B-Instruct` | Mellum 2 (12B MoE, ~2.5B active) | strong | JetBrains lane; loads via `AutoModelForMultimodalLM` |

Each tier is tagged, so a cast's `fast`/`balanced`/`strong` seats route to different sponsor
models. That cross-sponsor cast loads several multi-GB models per show — heavy on the free
ZeroGPU ~5-min/day budget and host RAM; on a dedicated GPU there is no cap. For a
quota-light demo, pin the whole cast to the tiny default. The tiny model is listed first, so
any untagged fallback also lands on the cheapest tier.

Bind a tier to a local model with a qualified key:

```yaml
profiles:
  tiny: { endpoint: "local:nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16", temperature: 0.7, max_tokens: 192 }
```

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
    max_tokens: 192
  # fast / balanced / strong follow the same shape (see the file). Note the
  # balanced/strong tiers are reasoning models (gemma4 reasoning parser) and carry
  # a much larger max_tokens so their thinking + answer fit (ADR-0023).
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

- `src/models/router.py` — `ModelRouter`, `ProfileSpec`, `_PROFILE_DECODING`, `_catalogue_spec()` (endpoint key → binding)
- `src/agents/base.py` — `ManifestAgent._route_key` (endpoint-or-tier)
- `src/core/registry.py` — `Registry.from_world()` (a UI/LLM-composed run on the same path)
- `src/models/litellm_provider.py` — `LiteLLMProvider` (live transport, real cost)
- `src/models/modal_catalogue.py` — engine view of the catalogue (key → binding)
- `src/models/inference.py` — unified backend registry (Modal · HF · Local GPU); qualified keys
- `src/models/local_catalogue.py` — local model catalogue + capability gate (`has_credentials`)
- `src/models/local_provider.py` — `LocalTransformersProvider`: in-process `@spaces.GPU` inference
- `src/core/manifest.py` — `resolve_model()` (env → catalogue default)
- `src/core/registry.py` — `build_router()`, `_resolve_model_endpoints()`, `_expand_env()`
- `src/models/provider.py` — `ModelProvider.last_usage`, `estimate_tokens()`
- `src/models/openai_compat.py` — `has_live_credentials()`, role→system personas
- `modal/catalogue.py` — the single source of truth: every served model + provider app
