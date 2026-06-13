# ADR-0032: A Third Inference Backend — Local llama.cpp (GGUF)

## Status

Superseded by ADR-0033. llama.cpp's persistent `llama-server` cannot hold a GPU under
ZeroGPU's per-call grant model; replaced by an in-process `transformers` backend that
works on any HF Space hardware (ADR-0024 *second inference backend / unified registry*,
ADR-0015 *LiteLLM gateway*, ADR-0022 *per-agent explicit model binding* remain in
force; this ADR is retained as a historical record only).

## Context

The engine had two live backends: vLLM endpoints we deploy on Modal (ADR-0015/0019)
and Hugging Face's serverless router (ADR-0024). Both run *somewhere else* — Modal needs
warmed GPUs, HF needs a token and a provider that serves the model. There was no way to
run a cast **entirely on the operator's own machine**, with no account, no token, and no
network after the first download.

llama.cpp's `llama-server` is exactly that: it loads a quantized **GGUF** model, runs it
on whatever hardware is present (Apple Metal, NVIDIA CUDA, or CPU), and exposes an
**OpenAI-compatible** API on `/v1`. Because it speaks the same REST surface as Modal/HF,
it slots into the existing LiteLLM gateway with no new transport code — the same seam
ADR-0024 was designed to leave open ("adding a third backend later touches only
`inference._BACKENDS`").

This also stacks hackathon lanes from one local server: the **Llama Champion** badge
(a real llama.cpp runtime in the cast), the **NVIDIA Nemotron Quest** (Nemotron 3 Nano
4B), and the **OpenBMB** track (MiniCPM 4.1 8B) — plus a JetBrains Mellum 2 thinking
model on the balanced tier. Every model stays within the ≤32B "small minds" rule, and the
4B Nemotron honours the ≤4B Tiny-Titan band.

## Decision

**A third backend = one more catalogue + a registry entry.** Add
`src/models/llamacpp_catalogue.py`, stdlib-only and offline-safe like its siblings, listing
the GGUF models with both engine-facing fields (`key` / `profile` / `params_b` /
`served_id`) and serving fields (`hf_repo` / `quant` / `ctx_size` / sampling /
`flash_attn` / `reasoning`). Its `binding_for()` yields the LiteLLM custom-endpoint form
`model = openai/<served_id>`, `base_url = $LLAMACPP_BASE_URL` (default
`http://127.0.0.1:8080/v1`), `api_key = $LLAMACPP_API_KEY` (a placeholder — llama-server
ignores it). Register it in `inference._BACKENDS` under the prefix `llamacpp`; qualified
keys are `llamacpp:<slug>` (e.g. `llamacpp:nemotron-3-nano-4b`). Nothing above the registry
changes — the router, the config loader's `endpoint:` expansion, the live/offline gate, and
the Lab picker all derive from the façade.

**The serving side is a separate, pure-where-it-matters launcher.** Add
`src/models/llamacpp_server.py`. `detect_accelerator(platform, probe)` returns
`metal` on macOS, `cuda` when `nvidia-smi` reports a GPU, else `cpu`;
`build_command(model, accelerator, …)` assembles the `llama-server` argv — pulling the
model by its `-hf` spec (downloaded on first run), serving it under `--alias <key>` so the
running server reports the stable id the engine binds to, and offloading **every layer to
the GPU** (`-ngl 999`) when one is present, omitting the flag on CPU. Both take their
environment as arguments so the GPU/CPU branches are testable with no GPU and no binary.
The `__main__` CLI launches a model by key and prints the matching `LLAMACPP_BASE_URL`
export.

**Opt-in by base URL, not a token.** A local server needs no auth, so the live/offline
gate (`has_credentials`) keys on `LLAMACPP_BASE_URL` being *set* — the launcher sets it,
or you export it to point at an already-running or remote server. With it unset the
backend never claims to be live, so the deterministic stub still owns the no-config demo.

## Consequences

- A cast can run fully local: `uv run python -m src.models.llamacpp_server
  nemotron-3-nano-4b`, export the printed URL, and the engine routes to it through the
  unchanged LiteLLM transport — no account, no token, GPU used automatically when present.
- Llama Champion + Nemotron + OpenBMB lanes are reachable from one server; the catalogue
  is plain data, so adding a GGUF or retuning a tier is a one-line `LlamaCppModel(...)` edit.
- Backward compatible by construction: bare keys still mean Modal, and the `llamacpp`
  prefix is new — existing config, manifests, and the green test baseline are unaffected.
- The engine still never names a vendor on a hot path: routing is by qualified key through
  one façade; the GGUF/quant churn is hidden behind `--alias <key>`.
- llama-server and the GGUF download are operator-side, never a Python dependency — the
  offline stub remains the default with no binary, no network, and extras uninstalled.
