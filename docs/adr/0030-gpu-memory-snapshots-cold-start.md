# ADR-0030: GPU Memory Snapshots and Keep-Warm for Cold-Start Latency

## Status

**Superseded by [ADR-0034 *Simplify the Modal serving layer*](0034-simplify-modal-serving-to-canonical-vllm.md)**
— the snapshot lifecycle was removed for being alpha and error-prone; cold starts
now rely on the shared compile/weight caches plus the retained `MODAL_LLM_KEEP_WARM`
demo switch. The historical context below stands.

Originally Accepted (extended [ADR-0014 *Modal model serving*](0014-modal-model-serving.md),
[ADR-0019](0019-single-model-catalogue-no-cloud-path.md))

## Context

Every served model scales to zero (`min_containers=0`), so the first request to
an idle endpoint pays the full cold-start pipeline: container boot → weight
download/load → engine warmup (CUDA-graph capture, compile cache). On the bigger
models this is **minutes**, which hurts in two places that matter for the
hackathon:

- **The live demo.** A judge's first click should not stare at a spinner while a
  14B model loads — the first 30 seconds are scored.
- **Iteration speed.** Every healthcheck or engine run against a cold workspace
  pays the same multi-minute tax per model.

The mitigations we already had are blunt: `min_containers` removes cold starts
entirely but burns GPU-hours around the clock, and the shared `vllm-cache`
Volume only amortizes *compilation* — weight load and warmup are still paid by
every cold container.

Modal's answer to exactly this is **memory snapshots**: checkpoint a booted
container (CPU state, and with the alpha `enable_gpu_snapshot` flag, GPU state)
and restore it on later cold starts instead of re-initializing. Modal's
documented vLLM recipe pairs snapshots with vLLM **sleep mode**: warm the engine,
offload weights to host RAM (`POST /sleep?level=1`), snapshot, then reload
weights on restore (`POST /wake_up`). Restores land in seconds.

The catch: the recipe requires a *class-based* lifecycle (`@modal.enter(snap=True)`
for the snapshotted warmup, `@modal.enter(snap=False)` for post-restore wake),
while our serving path (ADR-0014) registers plain `@app.function` web servers.
And GPU snapshots are alpha, with real constraints: single-GPU only, the model's
vLLM build must support sleep mode, and host RAM must hold the offloaded weights.

## Decision

**1. Snapshots are a per-model catalogue flag, not a global switch.**
`ModelConfig.gpu_snapshot: bool = False` in `modal/catalogue.py`. When set,
`service.register_model()` dispatches to a class-based registrar
(`_register_snapshot_model`) implementing Modal's recipe verbatim:

- `@modal.enter(snap=True)` — start `vllm serve` (with `--enable-sleep-mode`),
  wait for the port, run three warmup completions so compile/caching work lands
  *inside* the snapshot, then `POST /sleep?level=1`. `startup_timeout` bounds
  this whole phase (download + load + warmup + sleep).
- `@modal.enter(snap=False)` — `POST /wake_up` after every restore (it also
  runs on the snapshot-creating boot itself, which simply resumes serving).
- `@modal.web_server(..., label=cfg.endpoint_name)` — a no-op method that
  exposes the already-running vLLM port. The `label` pins the public URL to
  `…--<app>-<endpoint_name>.modal.run`, byte-identical to the function path, so
  clients, the engine catalogue (ADR-0019), and the DNS-label tests are
  untouched.
- Image gains `VLLM_SERVER_DEV_MODE=1` (exposes the sleep/wake endpoints) and
  `TORCHINDUCTOR_COMPILE_THREADS=1` (snapshot-safe compile), both scoped to
  snapshot models.

Helpers used by the class are **nested closures, not module functions**: the
class ships via cloudpickle (`serialized=True`), which pickles closures by value
but module-level functions by reference — and the `service` module doesn't exist
inside the container. (Verified by round-tripping the pickled class with the
local modules removed.)

**2. Conservative initial casting.** Snapshots are on for the well-behaved
single-GPU, native-vLLM models the cast hits hardest — `nemotron-3-nano-4b`
(tiny), `minicpm-4-1-8b` (fast), `nemotron-cascade-14b` (Judge specialist) — and
deliberately **off** where the recipe is unproven or impossible:

| Model | Why not |
| --- | --- |
| Gemma 4 12B / 26B | Nightly vLLM + Transformers modeling backend; sleep mode unverified on that path. |
| MiniCPM-o 4.5 | Omni-modal custom code path; kept conservative like its other knobs. |

Rolling back any model is a one-line `gpu_snapshot=False` — the plain function
path is untouched by this ADR.

**3. A deploy-time keep-warm switch for demo day.** `MODAL_LLM_KEEP_WARM=N`
(mirroring the existing `MODAL_LLM_REQUIRE_AUTH` / `MODAL_LLM_JSON_LOGS` idiom)
raises `min_containers` to N **for profile-bound models only** — the four tiers
the cast actually runs on. Specialists keep scale-to-zero. This is the
belt-and-braces for the hours around a live demo; snapshots are the everyday
path.

## Consequences

- Cold starts on snapshot models drop from minutes to seconds; scale-out under
  burst gets the same benefit (every new container is a restore, not a boot).
- The serving layer now has two registration shapes (function vs. class). Both
  are produced by the same loop from the same `ModelConfig`, and the dispatch is
  one `if` — but anyone debugging a snapshot model must know the lifecycle runs
  through `@modal.enter` hooks, not the function body.
- GPU snapshots are **Modal-alpha**. Known sharp edges we accept and guard:
  snapshots are invalidated by image changes (safe — they rebuild), restores can
  fail if Volume files used during snapshot are deleted, and the feature is
  single-GPU only (all snapshot models are `tensor_parallel_size=1`).
- The alpha surface is also an **API-stability risk**: the recipe is verified
  against the pinned Modal SDK (1.4.3 — `App.cls` accepts `serialized` /
  `enable_memory_snapshot` / `experimental_options`; `modal.web_server` accepts
  `label`), but `experimental_options={"enable_gpu_snapshot": True}` carries no
  compatibility promise. Re-verify these kwargs on every SDK bump; the per-model
  rollback (`gpu_snapshot=False`) restores the untouched plain path.
- The test suite covers registration, dispatch, and the cloudpickle round-trip,
  but a snapshot *restore* can only be observed live. Validate each snapshot
  model with `modal/healthcheck.py` after its first deploy — and again before
  demo day — rather than trusting the recipe on paper.
- The snapshot warmup makes three tiny completions at deploy-boot time; with
  auth enabled they authenticate via the same `VLLM_API_KEY` the secret injects,
  so `MODAL_LLM_REQUIRE_AUTH=1` keeps working.
- `MODAL_LLM_KEEP_WARM` left on costs real GPU-hours — it is documented as a
  demo-window switch, and the default deploy keeps scale-to-zero.
- First boot per snapshot model is slightly *slower* (warmup + sleep before
  serving), which is the right trade: it runs once per image/config change, not
  per cold start.
- Prize impact: this makes the Modal serving path credibly demo-ready
  (Modal Awards) and defends the first-30-seconds bar that Best Demo and
  Community Choice are scored on. The no-API-key deterministic stub is
  untouched, so the on-stage fallback stays reproducible.
