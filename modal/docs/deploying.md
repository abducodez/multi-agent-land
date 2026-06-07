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

Each model becomes its own OpenAI-compatible endpoint, named after its
`endpoint_name`:

```
https://<workspace>--<endpoint-name>.modal.run/v1
```

Standard routes: `/v1/chat/completions`, `/v1/completions`, `/v1/models`, plus
`/docs` for the Swagger UI. Smoke-test one:

```bash
python modal/client.py \
  --base-url https://<workspace>--gemma-4-12b.modal.run/v1 \
  --model google/gemma-4-12B \
  --prompt "Describe a mossy ticket booth in the wood."
```

## Configuring models (per task)

All knobs live in `registry.py` as `ModelConfig` fields — no serving code
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

Append one `ModelConfig` to the appropriate provider list in `registry.py`.

### Add a provider

1. Add a `<PROVIDER>_MODELS` list in `registry.py`.
2. Create `app_<provider>.py` that builds `modal.App("<provider>-llms")` and
   calls `register_all(app, <PROVIDER>_MODELS)`.

## Auth

Modal web endpoints are public by default. To require a bearer token, either:

- Set `VLLM_API_KEY` on the container (via a `modal.Secret`) so vLLM enforces
  `Authorization: Bearer <key>`; or
- Front the endpoint with Modal Proxy Auth Tokens
  (see `docs/modal-llms.txt` → Proxy Auth Tokens).

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

Endpoints are OpenAI-compatible, so the engine talks to them through the OpenAI
SDK. Point a model role at a deployed endpoint:

```bash
export OPENAI_BASE_URL="https://<workspace>--nemotron-3-nano-4b.modal.run/v1"
export OPENAI_API_KEY="EMPTY"   # or the configured VLLM_API_KEY
export MODEL_TINY="nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"
```

Map the engine's `MODEL_TINY/FAST/BALANCED/STRONG` tiers to the endpoints whose
size fits each role.
