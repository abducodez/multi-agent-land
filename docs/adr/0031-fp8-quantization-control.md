# ADR-0031: Env-Controlled FP8 Quantization for Modal Serving

## Status

**Superseded by [ADR-0034 *Simplify the Modal serving layer*](0034-simplify-modal-serving-to-canonical-vllm.md)**
— the env-controlled quantization machinery was removed; lower precision is now
reached via a model's `extra_vllm_args`. The historical context below stands.

Originally Accepted (extended [ADR-0014 *Modal model serving*](0014-modal-model-serving.md),
[ADR-0019](0019-single-model-catalogue-no-cloud-path.md); interacted with
[ADR-0030 *GPU memory snapshots*](0030-gpu-memory-snapshots-cold-start.md))

## Context

Every served model (ADR-0014) ships **BF16** weights — ~2 bytes/param. That sets a
hard floor on the GPU each model needs and on how much VRAM is left for the KV cache
(context length × concurrency). Two pressures push us toward lower precision:

- **Cost / fit.** Running a model at FP8 roughly halves its weight memory, so a model
  can fit a smaller (cheaper) GPU, or keep its GPU and gain KV-cache headroom.
- **Demo flexibility.** We want to A/B precision *per provider* during the hackathon
  without editing the catalogue or rebuilding our mental model of each endpoint.

vLLM already supports **on-the-fly FP8**: `--quantization fp8` quantizes BF16 weights
at load time (no pre-quantized checkpoint, no repo swap), and `--kv-cache-dtype fp8`
quantizes the KV cache independently. Both need an Ada/Hopper GPU — our L4 / L40S /
H200 all qualify — and vLLM support for the model's architecture.

The catch is that arch support is uneven. Custom-code / hybrid-mamba models
(Nemotron-H = `nemotron-3-nano-4b`/`-30b`, MiniCPM) and the Transformers-backend
Gemmas (the nightly-vLLM path; see ADR-0030's snapshot-exclusion table and the
catalogue's Gemma notes) may not serve under on-the-fly FP8 at all. A model
that can't will **fail to boot** — and on a snapshot model (ADR-0030) that surfaces as
the same `modal-http: invalid function call` a broken endpoint shows. So precision
can't be a blanket global default; it has to be opt-in and reversible per model and
per deploy.

## Decision

**1. Quantization is purely serving-side.** It only appends `--quantization` /
`--kv-cache-dtype` to the `vllm serve` argv in `service.build_command`. The
`--served-model-name` is unchanged, so the engine catalogue (ADR-0019), endpoint
URLs, and the running cast are *byte-identical* with or without it. Nothing in
`src/` changes.

**2. Two controls, env override wins.** Mirroring the `MODAL_LLM_KEEP_WARM` /
`MODAL_LLM_REQUIRE_AUTH` idiom (ADR-0030):

- **Per-model baseline** — `ModelConfig.quantization` / `kv_cache_dtype` in
  `catalogue.py` (both `str | None`, default `None` = full precision).
- **Per-deploy override** — `MODAL_LLM_QUANTIZATION` / `MODAL_LLM_KV_CACHE_DTYPE`,
  read once at module load in `service.py` and applied to *every* model in the
  deploy. A `_resolve_precision()` helper makes the override win over the per-model
  field; a disable token (`none`/`off`/`bf16`/`fp16`/`auto`/…) returns `None` so the
  flag is omitted and full precision is forced even on a model that defaults to
  quantized. Deploys are per-provider, so the override's blast radius is one app.

The override is read at **deploy time** (when `modal deploy` imports the app and
`build_command` runs), the same moment `KEEP_WARM` is read. The resolved argv is
what gets registered — including into the cloudpickled snapshot classes
(ADR-0030) — so the container never re-reads the env; changing precision is
always a redeploy, never drift in a running container. `scripts/deploy_modal.py`
surfaces it as `--quantization` / `--kv-cache-dtype` flags that set the env in the
deploy subprocess (`is not None`, so `--quantization none` is propagated, not dropped).

**3. Conservative initial casting: all per-model defaults stay `None`.** No model is
pinned to FP8 yet, because none has been verified to serve under it. FP8 is an
operator opt-in per provider; once a model is confirmed to boot and produce sane
output quantized, we can pin `quantization="fp8"` on it in the catalogue.

## Consequences

- Flipping a provider to FP8 is one flag (`--quantization fp8`) with no code edit;
  reverting is `--quantization none` or simply omitting it.
- A model whose arch rejects on-the-fly FP8 fails to boot under the override. This is
  why defaults stay `None` and why the docs tell you to verify per provider
  (`modal/healthcheck.py` / `curl <url>/v1/models`) after flipping it on, and redeploy
  without the flag if a model won't start. The failure is loud (no healthy container),
  not silent wrong output.
- FP8 is lossy. Output quality must be eyeballed per model before relying on it for a
  demo run — the tests assert the *flag wiring*, not generation quality (which can only
  be judged live).
- The env override is **all-or-nothing within a provider app**. A provider mixing
  FP8-capable and FP8-incapable archs can't be partially overridden at deploy time —
  pin the per-model `quantization` field for the capable models instead.
- Snapshot models (ADR-0030): the precision flags are baked into the snapshotted boot,
  so changing precision re-pays the one-time snapshot-creation warmup on the next
  deploy (no stale-precision restores — the snapshot is keyed to the new function
  version). A model that can't serve FP8 fails at snapshot *creation*, which is the
  same loud no-healthy-container failure as the plain path.
- **FP8 KV cache is incompatible with sleep-mode/snapshot models on the pinned vLLM.**
  `--kv-cache-dtype fp8` boots and snapshots fine, but the `/wake_up` path runs
  `init_fp8_kv_scales()` over a post-sleep KV cache that is a *list* of per-layer
  tensors (not one tensor), so `cache_tensor.zero_()` throws and every snapshot restore
  500s — an endpoint that boots but can never wake. This bit `nemotron-3-nano-4b`
  (`gpu_snapshot=True`) under a global `MODAL_LLM_KV_CACHE_DTYPE=fp8` deploy.
  `build_command` therefore **drops an FP8 `kv_cache_dtype` for any `gpu_snapshot`
  model** and warns: snapshot is a structural per-model decision, the KV dtype a deploy
  knob, so snapshot wins and the endpoint serves with full-precision KV cache. Weight
  `--quantization fp8` is a different code path and is unaffected. To actually run FP8
  KV cache on such a model, drop `gpu_snapshot` (trade the fast cold start for the KV
  win) — or revisit once the vLLM pin advances past the bug.
- Possible future unlock: FP8 weights halve the host-RAM needed for sleep level 1,
  which was the stated blocker for snapshotting `nemotron-3-nano-30b` (~60GB BF16,
  ADR-0030). Unverified — Nemotron-H may reject on-the-fly FP8 entirely — so this
  stays a note, not a plan.
- `tests/test_modal_build_command.py` is the first test to assert on `build_command`'s
  argv: it pins the per-model field, the env override precedence, and the force-disable
  token, plus the deploy-script env wiring. Zero mocks (plain `ModelConfig` in, argv
  list out).
- Prize impact: lower precision sharpens the Modal serving story (fit bigger small-
  models on cheaper GPUs, more KV-cache headroom) without touching the no-API-key
  deterministic stub, so the on-stage fallback stays reproducible.
