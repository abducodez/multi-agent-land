# ADR-0034: Simplify the Modal serving layer to the canonical vLLM recipe

## Status

Accepted. **Supersedes [ADR-0030 *GPU memory snapshots*](0030-gpu-memory-snapshots-cold-start.md)
and [ADR-0031 *FP8 quantization control*](0031-fp8-quantization-control.md).**
Extends [ADR-0014 *Modal model serving*](0014-modal-model-serving.md) and
[ADR-0019](0019-single-model-catalogue-no-cloud-path.md).

## Context

`modal/service.py` had grown to ~500 lines by accreting three optional
subsystems on top of the plain vLLM web-server path from ADR-0014:

- **GPU memory snapshots** (ADR-0030) — a class-based sleep→snapshot→wake
  lifecycle, a second registration shape, and `enable_gpu_snapshot` (Modal
  *alpha*).
- **FP8 / quantization control** (ADR-0031) — a deploy-time env-override resolver
  plus a workaround for FP8-KV-cache crashing the snapshot wake path.
- **Structured JSON logging** — a `vllm_logging.py` formatter shipped into the
  image and wired through a generated `dictConfig`.

In practice this surface was the source of the errors, not a benefit:

- The snapshot lifecycle is alpha and fragile — the documented FP8×snapshot
  wake-path crash (ADR-0031) is one instance; the hand-folded URL label and
  cloudpickled-closure constraints are others. Hard to deploy, hard to debug.
- The FP8 machinery defaulted to `None` on **every** model — pure surface area
  with no model actually using it.
- JSON logging defaulted **off** — more surface area, off by default.
- Per-model configs had drifted from the models' real serving requirements
  (e.g. the Gemma 4 26B was pinned to a nightly vLLM it doesn't need).

The working core is small and is exactly Modal's current canonical vLLM example:
an autoscaling `@app.function` + `@modal.concurrent` + `@modal.web_server` whose
body runs `subprocess.Popen(["vllm", "serve", ...])`.

## Decision

**1. One serving path.** `register_model()` only registers the plain
`@app.function` web server. The snapshot class lifecycle
(`_register_snapshot_model`, `_class_name`, sleep/wake, `enable_gpu_snapshot`) is
deleted. `service.py` drops from ~500 to ~210 lines.

**2. Quantization moves to the escape hatch.** The `MODAL_LLM_QUANTIZATION` /
`MODAL_LLM_KV_CACHE_DTYPE` env resolver, the `quantization` / `kv_cache_dtype`
`ModelConfig` fields, and the FP8×snapshot workaround are removed. A model that
wants lower precision passes the flags through the existing `extra_vllm_args`
(`("--quantization", "fp8")`). Quantization was always opt-in and never on; this
keeps it possible without standing machinery.

**3. JSON logging is removed.** `vllm_logging.py` is deleted along with the
`MODAL_LLM_JSON_LOGS` / `MODAL_LLM_LOG_LEVEL` wiring. Modal captures
stdout/stderr; `--enable-log-requests` (kept, via `log_requests`) gives
per-request detail.

**4. `ModelConfig` is trimmed** to the fields the one path actually reads.
Removed: `gpu_snapshot`, `quantization`, `kv_cache_dtype`, `max_num_seqs`,
`max_num_batched_tokens`, `target_concurrent_inputs`, `buffer_containers`,
`log_outputs`, `max_log_len`, `uvicorn_access_log`, `multimodal`. The autoscale
target is computed inline (~75% of `max_concurrent_inputs`); anything exotic uses
`extra_vllm_args`.

**5. Per-model configs re-grounded in each model's documentation** (verified
against the HF model cards + vLLM recipes, June 2026):

| Model | Correction |
| --- | --- |
| Gemma 4 **26B-A4B** | Standard `gemma4` MoE — serves on the **pinned stable vLLM**. Dropped the nightly pin, `transformers>=5.10.2`, the unverified `VLLM_USE_FLASHINFER_SAMPLER=0`, and `enforce_eager` (native path → CUDA graphs work). |
| Gemma 4 **12B** | `gemma4_unified` (encoder-free) has no class in any stable vLLM ≤0.22.1 → **keeps** `vllm_version="nightly"` + `transformers>=5.10.2`; dropped the unverified flashinfer env. |
| Nemotron Nano **4B / 30B** | Hybrid-Mamba; `trust_remote_code` kept. Served as plain chat — NVIDIA's `nano_v3` reasoning parser ships as a downloadable *plugin file* and is omitted for boot-robustness (addable via `extra_vllm_args` later). 30B params corrected 30→31. |
| Nemotron **Cascade-14B** | Confirmed stock Qwen3 — `reasoning_parser="qwen3"` + `tool_call_parser="hermes"` are correct and built-in; kept. |
| MiniCPM **4.1-8B** | `trust_remote_code` kept; no tool parser (custom `<|tool_call_start|>` format — engine uses guided decoding per ADR-0016). Serves on the pinned stable. |
| MiniCPM **-o 4.5** | Params corrected 8→9B; served text+image (audio over vLLM is experimental — the documented `transformers==4.51.0` pin conflicts with vLLM's bundled version, so we keep the lean preprocessing deps). |

## Consequences

- **Far smaller blast radius.** One registration shape, no alpha features, no
  generated log config, no precision resolver. The thing that errored is gone.
- **Cold starts** now rely on the always-on shared caches (weights + compiled
  graphs on Volumes) and the retained `MODAL_LLM_KEEP_WARM` demo-day switch
  (mechanism 2 of ADR-0030, the robust half). We trade snapshot's seconds-from-
  cold for simplicity; keep-warm covers the live-demo first-30-seconds bar.
- **Quantization / batch caps** are still reachable via `extra_vllm_args`, just
  not first-class fields. If a model later needs standing FP8, re-promote a typed
  field then — but not speculatively.
- **Gemma 4 26B is cheaper and more robust** off the nightly: it's a tier
  default (`strong`), so removing its nightly dependency removes a recurring
  break. Only the 12B remains on nightly, where it's unavoidable.
- **Prize impact unchanged.** All seven models and all four provider tracks
  (OpenAI-compatible, MiniCPM, Nemotron, Gemma) still deploy; the no-API-key
  deterministic stub is untouched. The serving path stays demo-ready for the
  Modal Awards, now without the alpha-feature risk on stage.
- **Tests** for the removed precision/snapshot behaviour are replaced by tests
  that pin the simplified `build_command` argv. Full suite stays green.
