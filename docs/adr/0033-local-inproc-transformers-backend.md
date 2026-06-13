# ADR-0033: Local In-Process `transformers` Backend (Supersedes ADR-0032)

**Status:** Accepted
**Date:** 2026-06-13
**Deciders:** project maintainers

## Context

ADR-0032 added a llama.cpp backend: a persistent `llama-server` process that exposes
an OpenAI-compatible HTTP endpoint and uses the operator's GPU for the lifetime of that
process. This model is structurally incompatible with Hugging Face **ZeroGPU**.

ZeroGPU grants a GPU *only for the duration of a `@spaces.GPU`-decorated function call*,
then reclaims it. A long-lived HTTP server needs to hold the GPU between requests — there
is no GPU to hold on ZeroGPU. The same mismatch rules out vLLM-as-a-server for the same
reason. Per the ZeroGPU documentation: the runtime is Gradio-SDK only; the GPU is an
NVIDIA RTX Pro 6000 Blackwell (48 GB `large` / 96 GB `xlarge`); anonymous users get ~2
minutes of free GPU time per day, authenticated users ~5 minutes; and the canonical usage
pattern is `transformers`/`diffusers` with model weights placed on `cuda` at module load
and the forward pass wrapped in `@spaces.GPU`. CUDA is *emulated* (no-op) outside the
decorated function and real inside it.

A second goal existed alongside ZeroGPU compatibility: the backend must be
**hardware-agnostic**. An HF Space can be assigned a **dedicated GPU** (T4, L4, L40S,
A100, …) or run on a local CUDA box. The solution should serve equally well in all three
environments — ZeroGPU, dedicated GPU, and local CUDA — without a code branch per
environment.

Replacing the llama.cpp GGUF path also means explicitly dropping the **Llama Champion**
bonus-quest badge (which required a real llama.cpp runtime in the cast). This is a
deliberate tradeoff: ZeroGPU compatibility and hardware-agnosticism are higher-value than
one badge.

`torch` and `transformers` already ship transitively via `sentence-transformers`.
(See the 2026-06-14 amendment below: `accelerate` was subsequently added to make
on-device loading robust — the only new dependency this backend required.)

## Decision

We will replace the llama.cpp backend with an in-process `transformers` backend that runs
model inference inside a `@spaces.GPU`-decorated call, caches loaded weights at module
level in the parent process so each ZeroGPU call inherits them without re-loading, and
gates availability on operator capability rather than a URL environment variable.

Four concrete sub-decisions:

**1. Non-HTTP router seam (`ProfileSpec.kind`).** `ProfileSpec` gains a `kind` field
(`"litellm"` | `"local"`). When `kind == "local"` the router dispatches to
`LocalTransformersProvider` directly, bypassing LiteLLM and HTTP entirely. This is the
first backend that does not go through the HTTP gateway — it is a clean extension of the
router seam left open by ADR-0024, not a hack around it.

**2. In-process forward pass with effect-free decorator.** `LocalTransformersProvider`
wraps the `transformers` forward pass in a module-level `@spaces.GPU(duration=<dynamic>)`
function. On ZeroGPU this decorator acquires and releases the GPU for that call's
duration. On a dedicated GPU or local CUDA box the decorator is a no-op (effect-free
passthrough), so the same code path is a persistent in-process provider on those
environments. No environment-specific branching in the provider.

**3. Two-phase load split across the fork (amended 2026-06-14 — see note).** Work is
split by where a GPU exists: the **parent** fetches weights to the on-disk HF cache
(`_ensure_downloaded` → `snapshot_download`, lazily on first `complete()`, recorded in a
`_DOWNLOADED` set) without ever touching CUDA or materialising the model in RAM; the
**worker** then loads the model *directly onto the granted GPU* inside the `@spaces.GPU`
call (`_ensure_loaded_on_device` → `from_pretrained(device_map={"": 0}, local_files_only=True)`)
and caches the device-resident model per repo in `_LOADED`. `torch` and `transformers`
imports stay lazy (never at module import) to prevent CUDA initialisation before the fork
and avoid tripping the PyTorch multiprocessing fork guard. The first call to a model pays a
disk→device materialise inside the window; later calls in a reused worker (and every call
on a dedicated GPU) hit the resident cache. *(Original decision: load weights on CPU in the
parent and let each forked call inherit them via copy-on-write, then `.to("cuda")` inside
the window. That crashed — see the amendment.)*

**4. Capability gate and per-run opt-in.** `local_catalogue.has_credentials()` gates on
one of three signals: `SPACES_ZERO_GPU` is set in env (HF ZeroGPU Space), or
`LOCAL_INFERENCE=1` is set (explicit operator opt-in on a dedicated GPU or local CUDA
box), or a cached `torch.cuda.is_available()` probe is true — but the auto-probe runs
only when the env argument is `None` or `os.environ` itself, so tests passing an explicit
env dict never import torch and stay deterministic without a GPU. Picking "Local GPU" in
the Lab backend radio is the per-run opt-in; when none of the three signals is present the
backend stays inactive and the deterministic stub owns the no-config demo path.

## Alternatives Considered

| Option | Pros | Cons |
|--------|------|------|
| Keep llama.cpp + server-per-request workaround | Preserves Llama Champion badge; GGUF models need no full-precision weights | Structurally incompatible with ZeroGPU; server startup latency per request; high complexity |
| vLLM-as-a-server on ZeroGPU | High throughput batching | Same persistent-process / per-call-GPU mismatch as llama.cpp |
| `llama-cpp-python` (in-process, no server) | Llama Champion badge preserved; GGUF quantisation | Additional heavy binary dep; GGUF format separate from HF model hub; weaker `transformers` ecosystem integration |
| In-process `transformers` (chosen) | Works on ZeroGPU, dedicated GPU, and local CUDA without branching; no new deps; full HF Hub model catalogue; keeps OpenBMB/MiniCPM and Tiny-Titan lanes | Drops Llama Champion badge; full-precision weights (larger VRAM footprint than GGUF quants) |

## Consequences

**Positive:**
- The Space runs on ZeroGPU free tier with no code changes — every forward pass
  naturally fits the per-call GPU grant model.
- The same binary runs on a dedicated GPU (T4/L4/L40S/A100) or local CUDA box with no
  env-branch; the decorator is transparent.
- No new Python dependencies — `torch` and `transformers` are already transitive deps.
- The parent-process cache means each ZeroGPU call after the first is weight-load-free
  within a session.
- **Prize-lane impact:** one sponsor family per tier, so a single in-process cast spans
  four tracks at once — **NVIDIA Nemotron** (`tiny`, also the **Tiny Titan** ≤4B lane via
  Nemotron-Mini-4B-Instruct), **OpenBMB / MiniCPM** (`fast`), **Cohere / Aya** (`balanced`),
  and **JetBrains / Mellum** (`strong`) — plus the **Community Choice** on-device-inference
  story for the HF Space demo.

**Negative / Risks:**
- **Llama Champion badge is explicitly dropped.** No llama.cpp runtime in the cast means
  this submission does not qualify for that bonus quest. This is a deliberate, accepted
  tradeoff.
- First call to a model in a fresh process incurs full weight-load latency. On ZeroGPU
  with the daily quota (~2–5 min GPU/day) this is a real cost on cold sessions.
- Full-precision (BF16/FP16) weights require more VRAM than GGUF quantised equivalents.
  The ZeroGPU `large` tier (48 GB) is the practical ceiling; models above ~28B BF16 will
  not fit without external quantisation.
- The ZeroGPU free-tier quota (≈5 min GPU/day authenticated) limits live demo length.
  A dedicated GPU Space eliminates this limit but costs credits.
- Lazy torch import means the first call also pays Python import overhead for
  `torch` + `transformers`. Acceptable for interactive demo pacing; unacceptable for
  low-latency production workloads.

**Neutral / Notes:**
- `llamacpp_catalogue.py` and `llamacpp_server.py` are deleted; `app.py`'s
  `gpu_selftest` `@spaces.GPU` guard is retained — it detects ZeroGPU availability at
  startup and is unrelated to the inference path.
- Each tier is tagged with a distinct sponsor model (NVIDIA Nemotron-Mini-4B-Instruct ·
  OpenBMB MiniCPM5-1B · Cohere Aya-Expanse-8B · JetBrains Mellum2-12B-A2.5B-Instruct), so
  a cross-sponsor cast runs on the Space's own GPU. This trades ZeroGPU quota/RAM headroom
  (several multi-GB loads per show) for multi-track coverage; the `tiny` model is listed
  first so any untagged fallback lands on the cheapest tier. Notes: Aya is a **gated** repo
  (needs licence acceptance + `HF_TOKEN`); the **whole in-process cast is native-arch** (no
  `trust_remote_code`) — the OpenBMB lane uses **MiniCPM5** (native `llama`, built for
  transformers 5.x) because the MiniCPM **4.x** custom-code models (authored for
  transformers ~4.56) mis-compute under the 5.x floor (KV-cache crash, then gibberish even
  with the cache off); MiniCPM4.1-8B remains available via the **Modal** vLLM lane, where
  its custom code runs under a pinned-compatible image. The NVIDIA tier uses
  Nemotron-**Mini** (a plain transformer), not the Nemotron-Nano hybrid, which hard-requires
  the mamba-ssm kernel that will not build on a Space. (The `is_torch_fx_available` /
  `is_torch_sdpa_available` v4-symbol shim is retained as defensive infrastructure for any
  future `trust_remote_code` model, though no current in-process model needs it.)
- Tests live in `tests/test_local_backend.py`. The full suite passes; the capability-gate
  logic — and the source-level placement contracts (CUDA only inside `@spaces.GPU`,
  device load via `device_map`, no meta-prone manual move) — are covered without a GPU or
  torch import in test processes.

## Amendment (2026-06-14): on-device loading via `device_map` + `accelerate`

The original sub-decision 3 — load weights on **CPU in the parent**, let each forked
`@spaces.GPU` call inherit them, then `model.to("cuda")` inside the window — crashed in
production with `NotImplementedError: Cannot copy out of meta tensor; no data!`.

**Root cause.** transformers 5.x always instantiates a model on the `meta` device and
streams the checkpoint onto the target device; `low_cpu_mem_usage` no longer alters this
(5.x silently drops the kwarg, so the earlier "force full materialisation" fix was a
no-op). After such a load, non-persistent buffers (e.g. a rotary `inv_freq`) and any
tied/"missing" head can still sit on `meta`. A subsequent `.to("cuda")` then tries to copy
those data-less tensors and dies (transformers#41038/#30703). The crash surfaced on
`CohereLabs/aya-expanse-8b` but is architecture-general; the `spaces` worker also *reuses*
across models, so a worker forked for one model loads a later one itself, outside the
parent's materialisation.

**Correction.** Hand transformers the device at load time via
`from_pretrained(device_map={"": 0}, …)` so it **materialises and places** every weight,
buffer and tied head on the device in one supported step (`_move_missing_keys_from_meta_to_device`
+ `initialize_weights`/`tie_weights` run on-device) — nothing is left on `meta`, and the
fragile post-hoc `.to("cuda")` is removed entirely. This requires **`accelerate`** (now a
declared dependency — the dependency claim in Context is amended accordingly). The load
moves into the `@spaces.GPU` window (the only place a GPU exists), so the parent's role
shrinks to a CUDA-free `snapshot_download`; `local_files_only=True` keeps the window off
the network. A side benefit: the parent no longer pins every cast model in host RAM
(previously ~60 GB for a four-model 8–12B cast), and the `@spaces.GPU` duration base is
raised to cover a cold on-device load.

## Related ADRs

- ADR-0032: llama.cpp local backend — superseded by this ADR; retained as historical record.
- ADR-0024: Hugging Face inference backend / unified registry — the `ProfileSpec.kind` seam this ADR extends.
- ADR-0015: LiteLLM gateway — bypassed by `kind="local"`; all other backends still go through it.
- ADR-0022: Per-agent explicit model binding — unchanged; `local:<repo_id>` qualified keys follow the same binding contract.
- ADR-0019: Single model catalogue — local catalogue follows the same `binding_for` / `has_credentials` interface.
