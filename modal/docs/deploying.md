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
| `max_concurrent_inputs` | Requests multiplexed onto one container before scaling out.    |
| `scaledown_window`      | Idle seconds before a container stops (cold-start vs. cost).   |
| `min_containers`        | Keep N warm to eliminate cold starts (always-on cost).         |
| `reasoning_parser` / `tool_call_parser` / `enable_auto_tool_choice` | OpenAI tool/reasoning features. |
| `multimodal` / `mm_limits` | Image/audio/video inputs and per-prompt caps.               |
| `trust_remote_code`     | Required by MiniCPM / Nemotron custom modeling code.           |
| `extra_vllm_args`       | Raw `vllm serve` flags appended verbatim (escape hatch).       |
| `extra_pip` / `env`     | Extra image deps / container env (escape hatch).               |

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
