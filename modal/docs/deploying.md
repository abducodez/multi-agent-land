# Deploying & configuring the model-serving apps

This guide covers prerequisites, deployment, configuration knobs, auth, GPU
sizing, and wiring the endpoints into the engine.

The serving layer is deliberately small: it's Modal's canonical vLLM recipe — an
autoscaling `@app.function` that launches `vllm serve` as a subprocess behind a
`@modal.web_server` — applied once in `service.py` to every model in
`catalogue.py`. See ADR-0034 for why we stripped the earlier snapshot / FP8 /
structured-logging machinery back to this core.

## Prerequisites

```bash
pip install -r modal/requirements.txt
modal token new            # one-time auth with your Modal workspace
```

Gated repos (Gemma, and the Nemotron repos here) require a Hugging Face token.
Accept each model's license on its Hugging Face page, then create the secret:

```bash
modal secret create huggingface-secret HF_TOKEN=hf_xxx
```

Only models with `gated=True` mount this secret; ungated models deploy without it.

## Deploy

Each provider is its own Modal app, deployed independently:

```bash
modal deploy modal/app_nvidia.py     # Nemotron 3 Nano 4B + 30B, Cascade 14B
modal deploy modal/app_openbmb.py    # MiniCPM4.1-8B + MiniCPM-o 4.5
modal deploy modal/app_google.py     # Gemma 4 12B + 26B
```

Use `modal serve modal/app_<provider>.py` for a hot-reloading dev session.

Or deploy one, several, or all providers with a single uv command — a thin
wrapper that exposes the two deploy-time env knobs as flags:

```bash
uv run scripts/deploy_modal.py                      # all providers
uv run scripts/deploy_modal.py nvidia openbmb       # just these
uv run scripts/deploy_modal.py nvidia --keep-warm   # = MODAL_LLM_KEEP_WARM=1
# --auth → MODAL_LLM_REQUIRE_AUTH=1, --dry-run to preview the commands.
```

Run these from the repo root; the script's own directory (`modal/`) is on
`sys.path`, so `from service import ...` / `from catalogue import ...` resolve,
and `import modal` still binds the installed SDK (the folder name does not
shadow it).

## Endpoints

Each model becomes its own OpenAI-compatible endpoint. Modal builds the URL from
the `modal.App` name **and** the function's `endpoint_name`:

```
https://<workspace>--<app-name>-<endpoint-name>.modal.run/v1
```

`<app-name>` is `nvidia-llms`, `openbmb-llms`, or `google-llms` (one per provider
app); `<endpoint-name>` is the per-model slug. e.g. the Nemotron 4B endpoint is
`https://<workspace>--nvidia-llms-nemotron-3-nano-4b.modal.run/v1`.

> **Model id vs URL slug.** The `--model` value (and the `"model"` field in a raw
> request) is the *served model id* — the HF repo id, e.g.
> `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` — because `served_model_name` defaults to
> the repo `name`. It is **not** the URL slug (`nemotron-3-nano-4b`). Call
> `/v1/models` on any endpoint to see the exact id it serves.

Standard routes: `/v1/chat/completions`, `/v1/completions`, `/v1/models`, plus
`/docs` for the Swagger UI. Smoke-test one:

```bash
python modal/client.py \
  --base-url https://<workspace>--google-llms-gemma-4-12b.modal.run/v1 \
  --model google/gemma-4-12B \
  --prompt "Describe a mossy ticket booth in the wood."
```

## Configuring models (per task)

All knobs live in `catalogue.py` as `ModelConfig` fields — no serving code
changes needed:

| Field                   | Purpose                                                        |
| ----------------------- | -------------------------------------------------------------- |
| `gpu`                   | Modal GPU spec, e.g. `H200:1`, `H100:2`, `L40S:1`, `L4:1`.     |
| `tensor_parallel_size`  | Shard across GPUs; set equal to the GPU count in `gpu`.        |
| `max_model_len`         | Cap context length to fit memory / tune throughput.            |
| `max_concurrent_inputs` | Hard ceiling of requests multiplexed onto one container (autoscale target is ~75% of it). |
| `scaledown_window`      | Idle seconds before a container stops (cold-start vs. cost).   |
| `min_containers`        | Keep N warm to eliminate cold starts (always-on cost).         |
| `gpu_memory_utilization` | Fraction of VRAM for weights + KV cache (vLLM default `0.9`); raise for a bigger KV cache. |
| `enable_prefix_caching` | Reuse the KV cache for shared prompt prefixes (on by default — big win when the system prompt / ledger context repeats across the cast). |
| `async_scheduling`      | Overlap CPU request scheduling with GPU compute (on by default; off for the Transformers-backend Gemma 12B + omni models). |
| `enforce_eager`         | Skip CUDA-graph capture — faster cold start, lower steady-state throughput. |
| `log_requests`          | Log each request's id, sampling params, and token counts (on by default). |
| `reasoning_parser` / `tool_call_parser` / `enable_auto_tool_choice` | OpenAI tool/reasoning features (vLLM parser names; leave None if unsupported). |
| `mm_limits`             | Per-prompt image/audio/video caps; set to 0 on an auto-detected-multimodal model you serve text-only. |
| `trust_remote_code`     | Required by MiniCPM / Nemotron custom modeling code.           |
| `vllm_version`          | Per-model inference-stack pin (escape hatch); `None` = the default `VLLM_VERSION`, `"nightly"` = latest nightly wheel, else a pinned version. |
| `extra_vllm_args`       | Raw `vllm serve` flags appended verbatim — the escape hatch for anything not modelled above (quantization, batch caps, custom parser plugins, …). |
| `extra_pip` / `env`     | Extra image deps / container env (escape hatch).               |

> **Per-model vLLM version.** The image pins `VLLM_VERSION` (see `service.py`) for
> reproducible deploys. A single model can override it via `vllm_version` when the
> pinned release can't serve its architecture — this is scoped to that model's image,
> so one model's bump never touches another provider's app. Only the Gemma 4 **12B**
> sets `vllm_version="nightly"` (plus `transformers>=5.10.2`) because its
> `gemma4_unified` architecture has no class in any stable vLLM ≤0.22.1. The Gemma 4
> **26B** is a standard MoE arch that serves on the pinned stable release, so it
> stays on the default pin.

### Performance tuning

The serving path follows Modal's high-performance-LLM-inference guidance, so the
defaults are already tuned for throughput; the knobs above let you push further
per model:

- **Prefix caching is on by default.** In a multi-agent cast the system prompt and
  shared ledger context repeat across nearly every call, so reusing the KV cache
  for that shared prefix is the single largest win — leave it on.
- **CUDA graphs are kept, their cost is amortized.** Containers capture CUDA
  graphs (no `enforce_eager`) for best steady-state throughput, and the compile /
  graph cache is persisted on the shared `vllm-cache` Volume (`VLLM_CACHE_ROOT`),
  so only the *first* container compiles — later cold starts replay the cached
  graphs. Set `enforce_eager=True` on a model only when its backend can't capture
  graphs (the Transformers-backend Gemma 12B) or when cold start dominates.
- **Async scheduling** overlaps CPU request scheduling with GPU compute; on by
  default for native vLLM models, off where the backend doesn't support it.
- **Autoscaling** scales out at ~75% of `max_concurrent_inputs` while a hot
  container bursts up to the ceiling, so we add capacity before a container
  saturates rather than after. Use `min_containers` to remove cold starts
  entirely (at always-on cost).

For memory-bound models, raise `gpu_memory_utilization` (more KV cache → more
concurrency); if a step OOMs, lower `max_model_len` or cap the batch via
`extra_vllm_args` (e.g. `("--max-num-seqs", "32")`).

### Cold starts

A scale-from-zero cold start pays container boot → weight load → engine warmup.
Two mechanisms keep that bounded:

**1. Shared caches (always on).** Weights are pulled once onto the
`huggingface-cache` Volume and the torch.compile / CUDA-graph artifacts are
persisted on the `vllm-cache` Volume (`VLLM_CACHE_ROOT`). So a model downloads
once across every container and provider, and only the *first* container
compiles its graphs — later cold starts replay the cache.

**2. Demo-day keep-warm (deploy-time, no code edits).** Pin one warm container
for every *profile-bound* model (tiny/fast/balanced/strong) right before a live
demo — specialists keep scale-to-zero:

```bash
MODAL_LLM_KEEP_WARM=1 modal deploy modal/app_nvidia.py   # one warm container per tier model
modal deploy modal/app_nvidia.py                         # back to scale-to-zero after
```

This burns GPU-hours while deployed; it's a switch for the hours around a demo,
not a steady state. `min_containers` in `catalogue.py` remains the per-model
override for anything finer-grained.

Cold-start clients must follow redirects: a Modal endpoint that hasn't answered
within ~150s returns a `303` to the same URL while the container finishes
booting (`modal/healthcheck.py` handles this; so does the engine's gateway).

### Add a model

Append one `ModelConfig` to the appropriate provider list in `catalogue.py` (tag
its `profile` tier to make it a tier default). The engine picks it up with no
edits — it reads the same `catalogue.py`.

### Add a provider

1. Add a `<PROVIDER>_MODELS` list and a `PROVIDERS["<provider>"]` entry (carrying
   its `app` name) in `catalogue.py`.
2. Create `app_<provider>.py` that reads that entry:
   `app = modal.App(PROVIDERS["<provider>"].app)` then
   `register_all(app, PROVIDERS["<provider>"].models)`.

## Lower precision (quantization)

Every model repo here ships **BF16** weights and serves at full precision. To
shrink a model's footprint — fit it on a smaller GPU, or free VRAM for a longer
context / more concurrency — pass vLLM's quantization flags through the
`extra_vllm_args` escape hatch on its `ModelConfig`:

```python
extra_vllm_args=("--quantization", "fp8", "--kv-cache-dtype", "fp8")
```

This is purely serving-side: `--served-model-name` is unchanged, so the engine,
endpoint URLs, and the running cast are untouched.

> **Not every architecture serves under on-the-fly FP8.** It needs an Ada/Hopper
> GPU (our L4/L40S/H200 all qualify) *and* vLLM support for the model's arch.
> Custom-code / hybrid-Mamba archs (the Nemotron Nanos, MiniCPM) and the
> Transformers-backend Gemma 12B may **fail to boot** under it. Verify a model
> after adding the flag (`modal/healthcheck.py` or `curl <url>/v1/models`); if it
> won't start, drop the flag. This is why every model defaults to full precision.

## Auth

Modal web endpoints are public by default. Secrets are supplied as environment
variables (never hard-coded). To require a bearer token:

```bash
# Key MUST be VLLM_API_KEY (vLLM reads it); value is the token clients send.
modal secret create llm-api-key VLLM_API_KEY=sk-your-token

# Turn auth on at deploy time — no code edits:
MODAL_LLM_REQUIRE_AUTH=1 modal deploy modal/app_google.py
```

When `MODAL_LLM_REQUIRE_AUTH` is set, every endpoint mounts the `llm-api-key`
secret as the `VLLM_API_KEY` env var and vLLM enforces `Authorization: Bearer
<token>` (401 otherwise). Clients pass the same token (the bundled `client.py`
reads it from `LLM_API_KEY`). Alternatively front endpoints with Modal Proxy
Auth Tokens (see `docs/modal-llms.txt` → Proxy Auth Tokens).

See [`openapi.md`](openapi.md) for the full API reference and the checked-in
OpenAPI spec (`../openapi.yaml`).

## Observability & logging

Every container's stdout/stderr is captured by Modal — watch it live with
`modal app logs <app-name>` or in the dashboard. Each endpoint runs vLLM with
`--enable-log-requests` (toggle via `log_requests`), so every call logs its
request id, sampling params, and (on completion) prompt/generation token counts
and finish reason. Clients can pass an `X-Request-Id` header and it shows up in
the request logs — handy for correlating an engine call with its server-side line.

Throughput, KV-cache usage, and prefix-cache hit rate are logged every second
(`VLLM_LOG_STATS_INTERVAL`) and also exposed as Prometheus metrics at `/metrics`.

## GPU sizing cheatsheet

BF16 weights ≈ 2 bytes/param; leave headroom for the KV cache. MoE models load
all expert weights even though only a slice activates per token, so size to the
total parameter count.

| Model                              | Params (total / active) | Starting GPU |
| ---------------------------------- | ----------------------- | ------------ |
| Nemotron-3-Nano-30B-A3B            | ~31B / ~3B (Mamba MoE)  | `H200:1`     |
| Nemotron-Cascade-14B-Thinking      | ~14B (dense, Qwen3)     | `L40S:1`     |
| Nemotron-3-Nano-4B                 | ~4B (Tiny Titan)        | `L4:1`       |
| MiniCPM-o-4_5 (omni)               | ~9B + media encoders    | `L40S:1`     |
| MiniCPM4.1-8B                      | 8B                      | `L40S:1`     |
| Gemma-4-26B-A4B-it                 | ~25B / ~4B (MoE)        | `H200:1`     |
| Gemma-4-12B-it                     | ~12B (dense)            | `L40S:1`     |

These are starting points. If a container OOMs, lower `max_model_len`, raise the
GPU tier, or bump `tensor_parallel_size` (and the GPU count) for sharding.

## Engine integration

The engine reads this same `catalogue.py` (by path, via
`src/models/modal_catalogue.py`) and routes every profile through the LiteLLM
gateway (ADR-0015 / ADR-0019). You don't wire endpoints by hand — set the
workspace and the four tiers bind automatically from `config/models.yaml`:

```bash
export MODAL_WORKSPACE="<your-workspace>"   # activates the live path
export MODAL_LLM_KEY="EMPTY"                # or the configured VLLM_API_KEY
```

Each profile's endpoint URL is derived as
`https://${MODAL_WORKSPACE}--<app>-<endpoint>.modal.run/v1`. To point a profile at
a different catalogue model, change its `endpoint:` in `config/models.yaml`; to
override the model string outright, set `MODEL_TINY/FAST/BALANCED/STRONG`. For a
one-off single endpoint (e.g. a local dev box), set `MODAL_LLM_BASE_URL` instead
of `MODAL_WORKSPACE`.
