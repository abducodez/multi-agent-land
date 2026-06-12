# Deploying & configuring the model-serving apps

This guide covers prerequisites, deployment, configuration knobs, auth, GPU
sizing, and wiring the endpoints into the engine.

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
modal deploy modal/app_nvidia.py     # Nemotron 3 Nano 30B + 4B
modal deploy modal/app_openbmb.py    # MiniCPM-o 4.5 + MiniCPM4.1-8B
modal deploy modal/app_google.py     # Gemma 4 26B + 12B
```

Use `modal serve modal/app_<provider>.py` for a hot-reloading dev session.

Or deploy one, several, or all providers with a single uv command — a thin
wrapper that exposes the deploy-time env knobs below as flags:

```bash
uv run scripts/deploy_modal.py                      # all providers
uv run scripts/deploy_modal.py nvidia openbmb       # just these
uv run scripts/deploy_modal.py nvidia --keep-warm   # = MODAL_LLM_KEEP_WARM=1
# --auth → MODAL_LLM_REQUIRE_AUTH=1, --json-logs → MODAL_LLM_JSON_LOGS=1,
# --log-level LEVEL → MODAL_LLM_LOG_LEVEL, --dry-run to preview the commands.
```

Run these from the repo root; the script's own directory (`modal/`) is on
`sys.path`, so `from service import ...` / `from registry import ...` resolve,
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
| `max_concurrent_inputs` | Hard ceiling of requests multiplexed onto one container.       |
| `target_concurrent_inputs` | Autoscale target — scale out here, burst to the max (defaults to ~75% of the ceiling). |
| `buffer_containers`     | Extra idle containers pre-warmed under active load (bursty traffic). |
| `scaledown_window`      | Idle seconds before a container stops (cold-start vs. cost).   |
| `gpu_snapshot`          | Serve via Modal memory snapshots (CPU + GPU): cold starts restore a warmed engine in seconds instead of re-paying load + warmup. See [Cold starts](#cold-starts). |
| `min_containers`        | Keep N warm to eliminate cold starts (always-on cost).         |
| `gpu_memory_utilization` | Fraction of VRAM for weights + KV cache (vLLM default `0.9`); raise for a bigger KV cache. |
| `enable_prefix_caching` | Reuse the KV cache for shared prompt prefixes (on by default — big win when the system prompt / ledger context repeats across the cast). |
| `async_scheduling`      | Overlap CPU request scheduling with GPU compute (on by default; off for the Transformers-backend Gemma + omni models). |
| `enforce_eager`         | Skip CUDA-graph capture — faster cold start, lower steady-state throughput. |
| `max_num_seqs` / `max_num_batched_tokens` | Batch-size and per-step token budget (memory vs. throughput). |
| `log_requests`          | Log each request's id, sampling params, and token counts (on by default). |
| `log_outputs`           | Also log generated text (verbose; off by default).            |
| `max_log_len`           | Truncate logged prompts/outputs to N chars (`None` = no cap; default 2048). |
| `uvicorn_access_log`    | Keep the per-request HTTP access line (method, path, status). |
| `reasoning_parser` / `tool_call_parser` / `enable_auto_tool_choice` | OpenAI tool/reasoning features. |
| `multimodal` / `mm_limits` | Image/audio/video inputs and per-prompt caps.               |
| `trust_remote_code`     | Required by MiniCPM / Nemotron custom modeling code.           |
| `vllm_version`          | Per-model inference-stack pin (escape hatch); `None` = the default `VLLM_VERSION`, `"nightly"` = latest nightly wheel, else a pinned version. |
| `extra_vllm_args`       | Raw `vllm serve` flags appended verbatim (escape hatch).       |
| `extra_pip` / `env`     | Extra image deps / container env (escape hatch).               |

> **Per-model vLLM version.** The image pins `VLLM_VERSION` (see `service.py`) for
> reproducible deploys. A single model can override it via `vllm_version` when the
> pinned release can't serve its architecture — this is scoped to that model's image,
> so one model's bump never touches another provider's app. The Gemma 4 entries set
> `vllm_version="nightly"` (plus `transformers>=5.10.2` and `VLLM_USE_FLASHINFER_SAMPLER=0`)
> because the `gemma4_unified` architecture is unservable on the pinned release.

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
  graphs (the Transformers-backend Gemma models) or when cold start dominates.
- **Async scheduling** overlaps CPU request scheduling with GPU compute; on by
  default for native vLLM models, off where the backend doesn't support it.
- **Autoscaling** scales out at `target_concurrent_inputs` (≈75% of the ceiling by
  default) while a hot container bursts up to `max_concurrent_inputs`, so we add
  capacity before a container saturates rather than after. Use `buffer_containers`
  to pre-warm spares for bursty traffic, or `min_containers` to remove cold starts
  entirely (at always-on cost).
- **The V1 engine is pinned** (`VLLM_USE_V1=1`) for its better scheduler, chunked
  prefill, and prefix caching.

For memory-bound models, raise `gpu_memory_utilization` (more KV cache → more
concurrency) and cap `max_num_seqs` / `max_num_batched_tokens` if a step OOMs.

### Cold starts

A scale-from-zero cold start normally pays the full pipeline: container boot →
weight load → engine warmup — minutes for the bigger models. Two mechanisms cut
this (ADR-0030):

**1. Memory snapshots (`gpu_snapshot=True`, per model).** The first container
boots once, loads weights, runs a few warmup completions, puts vLLM to sleep
(sleep level 1: weights offloaded to host RAM, KV cache dropped), and Modal
snapshots the container — CPU *and* GPU state. Every later cold start restores
the snapshot and wakes the engine, turning a multi-minute boot into seconds.
Under the hood this switches the model from the plain `@app.function` web server
to a class-based lifecycle (`@modal.enter(snap=True)` warmup → snapshot →
`@modal.enter(snap=False)` wake), but the public URL and API are identical —
clients can't tell the paths apart.

Snapshot-enabled today: `nemotron-3-nano-4b` (tiny), `minicpm-4-1-8b` (fast),
`nemotron-cascade-14b`. Left off deliberately: the Gemmas (nightly
Transformers-backend path, sleep mode unverified), `nemotron-3-nano-30b`
(~60GB of weights won't fit host RAM during sleep), and the omni specialist.
GPU snapshots are **Modal-alpha** — if a snapshot model misbehaves, set its
`gpu_snapshot=False` and redeploy; the plain path is unchanged.

**2. Demo-day keep-warm (deploy-time, no code edits).** Pin warm containers for
every *profile-bound* model (tiny/fast/balanced/strong) right before a live
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

## Quantization (lower precision)

Every model repo ships **BF16** weights. To shrink the memory footprint — fit a
model on a smaller GPU, or free VRAM for a longer context / more concurrency — you
can serve it at lower precision. This is purely serving-side: it only adds
`--quantization` / `--kv-cache-dtype` to the vLLM argv, and `--served-model-name`
is unchanged, so the engine, endpoint URLs, and the running cast are untouched.

Two controls, env override wins:

- **Per model** — set `quantization` (and/or `kv_cache_dtype`) on a `ModelConfig`
  in `catalogue.py`. This is the baseline a model serves at by default.
- **Per deploy (no code edits)** — `MODAL_LLM_QUANTIZATION` / `MODAL_LLM_KV_CACHE_DTYPE`
  override every model in the deploy. A disable token (`none`/`off`/`bf16`/…) forces
  full precision even on a model that defaults to quantized.

```bash
# On-the-fly FP8 weights for one provider (via the deploy helper):
uv run scripts/deploy_modal.py nvidia --quantization fp8

# FP8 weights + FP8 KV cache, raw modal CLI:
MODAL_LLM_QUANTIZATION=fp8 MODAL_LLM_KV_CACHE_DTYPE=fp8 modal deploy modal/app_nvidia.py

# Force full precision back (overrides any per-model default):
uv run scripts/deploy_modal.py nvidia --quantization none
```

> **Not every architecture serves under on-the-fly FP8.** It needs an Ada/Hopper
> GPU (our L4/L40S/H200 all qualify) *and* vLLM support for the model's arch.
> Custom-code / hybrid-mamba archs (Nemotron-H = `nemotron-3-nano-4b`/`-30b`,
> MiniCPM) and the Transformers-backend Gemmas may **fail to boot** under it — a
> failed boot surfaces as `modal-http: invalid function call` (no healthy
> container). Verify a provider after flipping it on (`modal/healthcheck.py` or
> `curl <url>/v1/models`); if a model won't start, redeploy that provider without
> the flag. This is why all per-model defaults stay `None` for now. See ADR-0031.

> **FP8 KV cache (`--kv-cache-dtype fp8`) is silently dropped for snapshot models.**
> On the pinned vLLM it crashes the `/wake_up` path (`init_fp8_kv_scales` →
> `'list' object has no attribute 'zero_'`), so an FP8-KV snapshot model boots but
> can never wake. `build_command` drops the flag for any `gpu_snapshot=True` model
> and logs a `⚠️` line at deploy; the endpoint serves with full-precision KV cache.
> FP8 *weights* (`--quantization fp8`) are unaffected. To run FP8 KV cache on such a
> model, set its `gpu_snapshot=False`. See ADR-0031.

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
`modal app logs <app-name>` or in the dashboard. Two layers shape what you see:

**Request-level detail (on by default).** Each endpoint runs vLLM with
`--enable-log-requests`, so every call logs its request id, sampling params, and
(on completion) prompt/generation token counts and finish reason. `--max-log-len`
caps the logged prompt at 2048 chars so a long context can't bloat a log line.
The uvicorn access log (method, path, status, latency) stays on. Tune per model:

| Knob              | Effect                                                        |
| ----------------- | ------------------------------------------------------------- |
| `log_requests`    | Per-request id / params / token counts (default **on**).      |
| `log_outputs`     | Also log the generated text — verbose, can echo story content (default off). |
| `max_log_len`     | Truncate logged prompts/outputs; set `None` to log them in full. |
| `uvicorn_access_log` | Set `False` to drop the per-request HTTP access line.      |

Clients can pass an `X-Request-Id` header and it shows up in the request logs —
handy for correlating an engine call with its server-side line.

**Structured JSON (opt-in).** For grepping fields or shipping to an aggregator,
emit one JSON object per log line instead of vLLM's coloured text. Turn it on at
deploy time — no code edits:

```bash
MODAL_LLM_JSON_LOGS=1 modal deploy modal/app_nvidia.py
MODAL_LLM_JSON_LOGS=1 MODAL_LLM_LOG_LEVEL=DEBUG modal deploy modal/app_google.py
```

This ships a dependency-free formatter (`modal/vllm_logging.py`) into the image
and points vLLM's `VLLM_LOGGING_CONFIG_PATH` at a generated `dictConfig`, so
**all** vLLM + uvicorn logs (including the request logs above) come out as JSON
with `ts` / `level` / `logger` / `msg` / `src` plus any structured extras (request
id, token counts). `MODAL_LLM_LOG_LEVEL` (default `INFO`) sets verbosity for both
the text and JSON paths. Leave JSON off for live demos — the coloured text is
easier to watch.

Throughput, KV-cache usage, and prefix-cache hit rate are logged every second
(`VLLM_LOG_STATS_INTERVAL`) and also exposed as Prometheus metrics at `/metrics`.

## GPU sizing cheatsheet

BF16 weights ≈ 2 bytes/param; leave headroom for the KV cache. MoE models load
all expert weights even though only a slice activates per token, so size to the
total parameter count.

| Model                              | Params (total / active) | Starting GPU |
| ---------------------------------- | ----------------------- | ------------ |
| Nemotron-3-Nano-30B-A3B            | 30B / ~3B (MoE)         | `H200:1`     |
| Nemotron-3-Nano-4B                 | 4B (Tiny Titan)         | `L4:1`       |
| MiniCPM-o-4_5 (omni)               | ~8B + media encoders    | `L40S:1`     |
| MiniCPM4.1-8B                      | 8B                      | `L40S:1`     |
| Gemma-4-26B-A4B-it                 | 26B / ~4B (MoE)         | `H200:1`     |
| Gemma-4-12B                        | 12B                     | `L40S:1`     |

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
