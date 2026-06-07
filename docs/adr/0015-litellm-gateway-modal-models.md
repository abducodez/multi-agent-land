# ADR-0015: LiteLLM Gateway for Modal-Served Small Models

## Status

Accepted

## Context

Per-agent model routing (ADR-0010) resolves each agent's logical profile
(`tiny`/`fast`/`balanced`/`strong`) to a concrete model behind an
OpenAI-compatible interface, and ADR-0005 added a Modal serving layer that exposes
small models (all ≤32B, with a ≤4B Tiny Titan tier) as autoscaling, vLLM-backed,
OpenAI-compatible HTTP endpoints. Until now the live transport was a hand-rolled
`openai` SDK call (`OpenAICompatProvider`) and there was no real cost signal: the
Governor's `hourly_budget_usd` (ADR-0007, ADR-0013) could only ever see `0.0`.

We want one gateway that (a) routes every profile through a single, idiomatic
call, (b) reaches the self-served Modal/vLLM endpoints without per-vendor
branching, and (c) reports the real per-call cost so spend caps become
enforceable — while keeping the offline path fully deterministic and free of any
new dependency.

## Decision

Introduce a **LiteLLM gateway** as the live *transport*. This replaces how a model
is *called*, not the routing abstraction: `ModelRouter.for_profile(profile) ->
ModelProvider` and `ManifestAgent`'s usage are unchanged.

**Thin, standard provider.** `LiteLLMProvider(ModelProvider)`
(`src/models/litellm_provider.py`) issues a single
`litellm.completion(model=…, api_base=…, api_key=…, messages=[{system},{user}],
temperature=…, max_tokens=…)` call. The call is deliberately idiomatic so a later
layer can wrap it (e.g. `instructor.from_litellm(litellm.completion)`) without
fighting this code. `litellm` is imported lazily inside `complete()`, so importing
`src.models.*` (and `app`) never requires the package. On error it mirrors
`OpenAICompatProvider`: zero the usage and return a `"[model error: …]"` string.

**Profiles → Modal endpoints.** Each profile binds to one served endpoint from
`modal/registry.py`:

| Profile | Modal endpoint | Served model id |
|---|---|---|
| `tiny` | `nemotron-3-nano-4b` | `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` |
| `fast` | `minicpm-4-1-8b` | `openbmb/MiniCPM4.1-8B` |
| `balanced` | `gemma-4-12b` | `google/gemma-4-12B` |
| `strong` | `gemma-4-26b` | `google/gemma-4-26B-A4B-it` |

For an OpenAI-compatible custom endpoint the LiteLLM model string is
`openai/<served_model_id>` with `api_base` set to the endpoint's `/v1` URL. A
self-served vLLM endpoint accepts any token, so the key defaults to the
conventional `"EMPTY"` when unset.

**Workspace is not hard-coded.** Modal serves each endpoint at a distinct
subdomain `https://<workspace>--<app>-<endpoint>.modal.run/v1` (app = nvidia-llms /
openbmb-llms / google-llms), so a single base URL cannot address all four.
`config/models.yaml` templates only the deploy-specific workspace:
`base_url: https://${MODAL_WORKSPACE}--<app>-<endpoint>.modal.run/v1` and
`api_key: ${MODAL_LLM_KEY}`. `Registry.from_dir()` expands these on load
(`_expand_env`); if any referenced var is unset the whole string collapses to `""`
(an incomplete binding is *not configured* rather than a broken half-URL) and a
validator nulls it. `ModelProfileConfig`/`ProfileSpec` gained `api_key` alongside
the existing `base_url`.

**Real cost → Governor.** The provider reads cost from
`response._hidden_params["response_cost"]`, falling back to
`litellm.completion_cost(response)` — both guarded, so an unpriced/self-served
model simply yields `0.0` instead of raising. Cost is exposed on
`last_usage["cost_usd"]` (and `last_cost`); `ManifestAgent` carries it on its
`last_usage`, and the conductor passes it to `governor.record_call(tokens=…,
cost_usd=…)`. `hourly_budget_usd` is now a real spend cap on the live path.

**Env-gated, offline by default.** `has_live_credentials()` stays the single
online/offline decision and now also treats `MODAL_WORKSPACE` /
`MODAL_LLM_BASE_URL` as an activating signal (in addition to `OPENAI_API_KEY`).
With none set, the router serves `DeterministicTinyModel` for every profile, live
bindings are ignored, and cost is `0.0`. `litellm` is an optional `litellm` extra
in `pyproject.toml`.

## Consequences

- A hosted deployment sets `MODAL_WORKSPACE` (and optionally `MODAL_LLM_KEY`) and
  every profile routes through LiteLLM to its Modal endpoint, with real cost
  metered into the Governor. Nothing else changes.
- The offline path is the default and import-clean: the full suite passes with no
  credentials, no network, and `litellm` not installed.
- `OpenAICompatProvider` is retained for its role→system persona map (reused by the
  gateway) and `has_live_credentials()`; it is no longer the live transport.
- The standard `litellm.completion(...)` shape leaves the door open for the
  follow-up Instructor change to wrap the same client for structured output.
- Cost accuracy depends on LiteLLM's pricing database; self-served vLLM models are
  unpriced and report `0.0`. Token caps (`max_total_tokens`) remain the budget
  guard for those; attaching `custom_cost_per_token` per endpoint is a follow-up.
- A single `MODAL_LLM_BASE_URL` activates the live path but points only one URL;
  multi-endpoint routing uses `MODAL_WORKSPACE` templating. Keeping both is
  intentional (one-endpoint smoke tests vs. the full four-tier cast).
