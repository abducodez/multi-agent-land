"""The single source of truth for every servable model — provider-agnostic data.

This module is **stdlib-only**: it does *not* ``import modal`` and does not touch
the serving path. That is deliberate. Two very different consumers read it:

  * the **serving side** (``service.py`` + ``app_<provider>.py``) turns each
    :class:`ModelConfig` into an autoscaling, OpenAI-compatible vLLM endpoint on
    Modal; and
  * the **engine** (``src/models/modal_catalogue.py``) reads the same catalogue to
    learn which models exist and how to *call* them — deriving each profile's
    LiteLLM model string and endpoint URL from the data here, so a model added in
    one place is immediately usable by the cast.

Because the engine cannot ``import modal`` (the folder name would shadow the PyPI
SDK), it loads this file *by path*. Keeping the catalogue free of any Modal/vLLM
import is what makes that load cheap, offline-safe, and dependency-free — so
nothing here may grow a heavy import.

Add a model = append one :class:`ModelConfig` to a provider list below. Add a
provider = add one :class:`Provider`. Everything downstream (the deployed
endpoint, the URL the engine calls, the docs table) derives from this data.

GPU sizing notes (starting points — tune against real memory use):
- BF16 weights ≈ 2 bytes/param. Leave headroom for the KV cache.
- MoE models (A3B / A4B) load all expert weights but only activate a slice,
  so size GPU memory to the *total* parameter count, not the active count.
- Cap ``max_model_len`` to trade context length for KV-cache memory / throughput.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Model configuration -------------------------------------------------------


@dataclass(frozen=True)
class ModelConfig:
    """Everything needed to serve one model as an OpenAI-compatible endpoint.

    Construct one of these in a provider list below. The serving layer
    (``service.py``) reads the hardware/inference/scaling fields; the engine
    reads ``endpoint_name`` / ``served_name`` / ``profile`` / ``params_b`` to call
    it. Nothing else needs to change to add a model.
    """

    # Identity
    name: str  # Hugging Face repo id, e.g. "google/gemma-4-12B"
    endpoint_name: str  # Modal function + URL slug, e.g. "gemma-4-12b"; also the engine casting key
    served_model_name: str | None = None  # model id clients pass; defaults to `name`
    revision: str | None = None  # pin a commit for reproducibility

    # Logical role (engine-facing). The tier this model is the default casting for
    # (tiny ≤4B / fast ≤7B / balanced ≤13B / strong ≤32B), or None for an
    # alternate/specialist model not bound to a profile by default.
    profile: str | None = None
    params_b: float | None = None  # total parameter count in billions (docs / Tiny-Titan checks)

    # Hardware
    gpu: str = "L40S:1"  # Modal GPU spec, e.g. "H200:1", "H100:2", "L4:1"
    tensor_parallel_size: int = 1  # set to GPU count for multi-GPU sharding

    # Inference-stack override (escape hatch). ``None`` uses the serving layer's
    # pinned ``VLLM_VERSION`` (the reproducible default). ``"nightly"`` installs the
    # latest vLLM nightly wheel; any other string is a pinned version (e.g.
    # ``"0.23.0"``). Use only when a model needs a build the default pin can't serve
    # — e.g. Gemma 4's ``gemma4_unified`` arch, unservable on 0.21.0. Scoped per
    # model, so one model's bump never touches another provider's app.
    vllm_version: str | None = None

    # Inference shape
    max_model_len: int | None = None  # cap context to fit memory / task
    trust_remote_code: bool = False  # required by MiniCPM / Nemotron custom code

    # Precision / quantization (vLLM serve flags). Both default to full precision
    # (BF16 weights, model-dtype KV cache); set them to shrink the memory footprint
    # so a model fits a smaller GPU or leaves more room for KV cache. A deploy-time
    # env override (``MODAL_LLM_QUANTIZATION`` / ``MODAL_LLM_KV_CACHE_DTYPE``, read in
    # ``service.py``) wins over these per-model values for a whole deploy. CAVEAT:
    # on-the-fly FP8 needs an Ada/Hopper GPU (our L4/L40S/H200 all qualify) AND vLLM
    # support for the architecture — custom-code / hybrid-mamba archs (Nemotron-H,
    # MiniCPM) and the Transformers-backend Gemmas may fail to start under it, so these
    # stay ``None`` until a model is verified to serve quantized. See ADR-0031.
    quantization: str | None = None  # vLLM --quantization, on-the-fly weight quant (e.g. "fp8"); None = full BF16
    kv_cache_dtype: str | None = None  # vLLM --kv-cache-dtype (e.g. "fp8"); None = auto (model dtype)

    # Performance / throughput (vLLM serve flags). Defaults target high
    # steady-state throughput on the common single-GPU path; tune per model.
    # See ``service.build_command`` for how each maps to a flag.
    gpu_memory_utilization: float | None = None  # fraction of VRAM for weights + KV cache (vLLM default 0.9)
    enable_prefix_caching: bool = True  # reuse KV for shared prompt prefixes — big win when system/context repeat
    async_scheduling: bool = True  # overlap CPU request scheduling with GPU compute
    enforce_eager: bool = False  # skip CUDA-graph capture: faster cold start, lower steady-state throughput
    max_num_seqs: int | None = None  # cap sequences batched per step (memory vs. throughput)
    max_num_batched_tokens: int | None = None  # token budget per scheduler step (prefill throughput)

    # Cold starts. Opt a model into Modal memory snapshots (CPU + experimental GPU
    # snapshot): the container boots once, loads weights, warms the engine, puts it
    # to sleep (vLLM sleep mode, weights offloaded to host RAM), and is snapshotted;
    # every later cold start restores the snapshot and wakes the engine in seconds
    # instead of re-paying download + load + warmup. Constraints (why this is per
    # model, not global): single-GPU models only, the model's vLLM build must
    # support `--enable-sleep-mode`, and host RAM must hold the offloaded weights.
    # Modal marks GPU snapshots alpha — keep it off for exotic serving paths
    # (Transformers-backend Gemma, the omni specialist) and flip off on any model
    # that misbehaves; the plain serving path is unchanged.
    gpu_snapshot: bool = False

    # Observability / request logging (vLLM serve flags). Defaults give per-request
    # visibility in the container logs out of the box; see ``service.build_command``.
    log_requests: bool = True  # log each request's id, sampling params, and token counts
    log_outputs: bool = False  # also log generated text (verbose; can echo story content) — opt-in
    max_log_len: int | None = 2048  # truncate logged prompts/outputs to N chars (None = no cap)
    uvicorn_access_log: bool = True  # keep uvicorn's per-request HTTP access line (method, path, status)

    # OpenAI feature parsers (vLLM names; leave None if unsupported on the model)
    reasoning_parser: str | None = None
    tool_call_parser: str | None = None
    enable_auto_tool_choice: bool = False

    # Multimodal
    multimodal: bool = False
    mm_limits: dict[str, int] | None = None  # e.g. {"image": 4, "audio": 2}

    # Scaling / lifecycle
    max_concurrent_inputs: int = 64  # hard ceiling of requests multiplexed onto one container
    target_concurrent_inputs: int | None = None  # autoscale target — scale out here, burst up to max; defaults to ~75%
    buffer_containers: int = 0  # extra idle containers to pre-warm under active load (bursty traffic)
    scaledown_window: int = 15 * 60  # idle seconds before a container stops
    min_containers: int = 0  # keep N warm to remove cold starts (costs $)
    startup_timeout: int = 30 * 60  # weight download + load can be slow
    request_timeout: int = 30 * 60  # max seconds a single request may run

    # Access
    gated: bool = False  # repo needs a Hugging Face token

    # Escape hatches
    extra_vllm_args: tuple[str, ...] = ()  # raw flags appended verbatim
    env: dict[str, str] = field(default_factory=dict)  # extra container env
    extra_pip: tuple[str, ...] = ()  # extra deps (audio/vision backends, etc.)

    @property
    def served_name(self) -> str:
        return self.served_model_name or self.name


# --- Provider grouping ---------------------------------------------------------


@dataclass(frozen=True)
class Provider:
    """One isolated Modal app and the models it serves.

    The ``app`` name is half of every endpoint URL
    (``https://<workspace>--<app>-<endpoint_name>.modal.run/v1``), so it lives
    here — the single place app name and model list are paired — and both the
    ``app_<provider>.py`` deploy file and the engine read it from here.
    """

    key: str  # short handle, e.g. "nvidia"
    app: str  # modal.App name, e.g. "nvidia-llms"
    label: str  # display name, e.g. "NVIDIA"
    models: tuple[ModelConfig, ...]


# --- NVIDIA (Nemotron) ---------------------------------------------------------

NVIDIA_MODELS: tuple[ModelConfig, ...] = (
    ModelConfig(
        name="nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16",
        endpoint_name="nemotron-3-nano-4b",
        # Tiny Titan tier (≤4B): comfortably fits a single 24GB L4.
        profile="tiny",
        params_b=4,
        gpu="L4:1",
        max_model_len=16384,
        trust_remote_code=True,
        gated=True,
        max_concurrent_inputs=32,
        # Tiny tier is the cast's hottest endpoint and 4B of BF16 weights (~8GB)
        # easily fit host RAM during sleep — the ideal snapshot candidate.
        gpu_snapshot=True,
    ),
    ModelConfig(
        name="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16",
        endpoint_name="nemotron-3-nano-30b",
        # 30B total params in BF16 (~60GB) though only ~3B activate per token.
        # An alternate strong model — not cast to a profile by default.
        # No gpu_snapshot: sleep mode would offload ~60GB of weights to host RAM,
        # past what a default container comfortably holds.
        params_b=30,
        gpu="H200:1",
        max_model_len=32768,
        trust_remote_code=True,
        gated=True,
        max_concurrent_inputs=64,
    ),
    ModelConfig(
        name="nvidia/Nemotron-Cascade-14B-Thinking",
        # Keep the slug short: the public URL is one DNS label
        # (<workspace>--<app>-<endpoint_name>.modal.run) capped at 63 chars, and a
        # longer "...-thinking" slug pushed it to 65 on a normal workspace, so the
        # host failed to resolve. The thinking-only nature is documented below, not
        # in the slug. See endpoint_url() and tests/test_modal_endpoint_urls.py.
        endpoint_name="nemotron-cascade-14b",
        # Dense 14B reasoning model built on Qwen3-14B Base; thinking-only. BF16
        # weights (~28GB) plus KV cache fit a single 48GB L40S. A specialist
        # model — left unbound so it can be cast explicitly at a reasoning-heavy
        # agent (e.g. the Judge) without displacing a tier default.
        params_b=14,
        gpu="L40S:1",
        max_model_len=32768,
        # Qwen3-native in vLLM (no custom code); ChatML template with a thinking
        # block parsed by the Qwen3 reasoning parser.
        reasoning_parser="qwen3",
        tool_call_parser="hermes",
        enable_auto_tool_choice=True,
        max_concurrent_inputs=48,
        # Qwen3-native single-GPU path on the pinned vLLM — snapshot-safe, and a
        # reasoning model is exactly where a multi-minute cold start hurts most.
        gpu_snapshot=True,
    ),
)

# --- OpenBMB (MiniCPM) ---------------------------------------------------------

OPENBMB_MODELS: tuple[ModelConfig, ...] = (
    ModelConfig(
        name="openbmb/MiniCPM4.1-8B",
        endpoint_name="minicpm-4-1-8b",
        profile="fast",
        params_b=8,
        gpu="L40S:1",
        max_model_len=32768,
        trust_remote_code=True,
        max_concurrent_inputs=48,
        # Fast tier default for the cast; 8B BF16 (~16GB) offloads to host RAM
        # fine. Sleep mode is allocator-level, so the custom MiniCPM modeling
        # code doesn't affect it.
        gpu_snapshot=True,
        # No tool_call_parser on purpose: MiniCPM4.1 emits a custom
        # <|tool_call_start|> format vLLM 0.21.0 has no parser for, so tool-call
        # structured output 400s here. The engine's structured path uses vLLM
        # guided decoding (response_format json_schema) instead, which is
        # parser-independent — see ADR-0016. Don't bolt on a mismatched parser.
    ),
    ModelConfig(
        name="openbmb/MiniCPM-o-4_5",
        endpoint_name="minicpm-o-4-5",
        # Omni-modal (text + vision + audio). Needs custom code and media backends.
        # A specialist model — not cast to a profile by default.
        params_b=8,
        gpu="L40S:1",
        trust_remote_code=True,
        multimodal=True,
        mm_limits={"image": 4, "audio": 2, "video": 1},
        # Audio/vision preprocessing backends pulled into the image.
        extra_pip=("librosa", "soundfile", "timm"),
        max_concurrent_inputs=16,
        # Custom omni-modal code path: keep the async scheduler off (conservative
        # — it's a specialist, not on the default cast). Prefix caching stays on.
        async_scheduling=False,
    ),
)

# --- Google (Gemma) ------------------------------------------------------------

GOOGLE_MODELS: tuple[ModelConfig, ...] = (
    ModelConfig(
        # Instruction-tuned repo — the right checkpoint for a balanced agent (the
        # base ``google/gemma-4-12B`` is pretrained-only). Both repos share the
        # ``gemma4_unified`` architecture, which vLLM 0.21.0 has no dedicated class
        # for, so it runs via the Transformers modeling backend either way.
        name="google/gemma-4-12B-it",
        # Keep the client-facing id stable (engine/tests/docs already use it); vLLM
        # serves the -it weights under this alias via --served-model-name.
        served_model_name="google/gemma-4-12B",
        endpoint_name="gemma-4-12b",
        profile="balanced",
        params_b=12,
        gpu="L40S:1",
        max_model_len=32768,
        gated=True,
        reasoning_parser="gemma4",
        tool_call_parser="gemma4",
        enable_auto_tool_choice=True,
        max_concurrent_inputs=48,
        # Served via vLLM's Transformers modeling backend (gemma4_unified has no
        # native vLLM class), which runs eager-only — CUDA-graph capture and the
        # async scheduler aren't supported on that path, so disable both here.
        # Prefix caching still applies and stays on (the default). gpu_snapshot
        # stays off too: sleep mode on the nightly Transformers backend is
        # unverified, and the Gemmas already skip the costliest warmup (no
        # CUDA-graph capture).
        enforce_eager=True,
        async_scheduling=False,
        # Text-only in the cast (vision/audio is the MiniCPM-o specialist's job).
        # vLLM auto-detects gemma4_unified as multimodal and otherwise spends a big
        # slice of cold-start profiling a *video* encoder we never call (and the MM
        # warmup fails anyway). Zeroing the per-prompt MM limits disables that whole
        # path — faster start, less GPU memory, more KV cache.
        mm_limits={"image": 0, "audio": 0, "video": 0},
        # gemma4_unified uses *variable* head dims (256 on sliding-attention layers,
        # 512 on full-attention ones). vLLM <= 0.22.1 (incl. the pinned 0.21.0) sizes
        # the o_proj from a uniform head_dim and dies on the full-attention layers
        # with "mat1 and mat2 shapes cannot be multiplied". Only a vLLM nightly serves
        # gemma4_unified, paired with transformers >= 5.10.2 (which adds the arch) and
        # the FlashInfer sampler off (its JIT path breaks on these builds). All three
        # are scoped to this model, so NVIDIA/OpenBMB stay on the reproducible pin.
        vllm_version="nightly",
        extra_pip=("transformers>=5.10.2",),
        env={"VLLM_USE_FLASHINFER_SAMPLER": "0"},
    ),
    ModelConfig(
        name="google/gemma-4-26B-A4B-it",
        endpoint_name="gemma-4-26b",
        # MoE: ~26B total params (~4B active). Gated repo — needs an HF token.
        profile="strong",
        params_b=26,
        gpu="H200:1",
        max_model_len=32768,
        gated=True,
        reasoning_parser="gemma4",
        tool_call_parser="gemma4",
        enable_auto_tool_choice=True,
        max_concurrent_inputs=64,
        # Transformers modeling backend (see the 12B above): eager-only, so no
        # CUDA graphs / async scheduler. Prefix caching stays on by default.
        enforce_eager=True,
        async_scheduling=False,
        # Text-only in the cast — disable the auto-detected multimodal (video)
        # encoder to cut cold-start profiling and free memory (see the 12B above).
        mm_limits={"image": 0, "audio": 0, "video": 0},
        # Same gemma4_unified fix as the 12B above (nightly vLLM + transformers
        # >= 5.10.2 + FlashInfer sampler off).
        vllm_version="nightly",
        extra_pip=("transformers>=5.10.2",),
        env={"VLLM_USE_FLASHINFER_SAMPLER": "0"},
    ),
)

# --- Provider registry ---------------------------------------------------------

PROVIDERS: dict[str, Provider] = {
    "nvidia": Provider(key="nvidia", app="nvidia-llms", label="NVIDIA", models=NVIDIA_MODELS),
    "openbmb": Provider(key="openbmb", app="openbmb-llms", label="OpenBMB", models=OPENBMB_MODELS),
    "google": Provider(key="google", app="google-llms", label="Google", models=GOOGLE_MODELS),
}

# Convenience: every model across providers (handy for tooling / docs).
ALL_MODELS: tuple[ModelConfig, ...] = tuple(m for p in PROVIDERS.values() for m in p.models)


# --- Engine-facing view --------------------------------------------------------


@dataclass(frozen=True)
class CatalogueEntry:
    """Flat, JSON-safe view of one served model — everything needed to *call* it.

    The engine builds its profile bindings from these (it never needs the full
    serving :class:`ModelConfig`), so adding a model here makes it bindable with
    no engine edits. ``key`` is the casting handle a profile points at.
    """

    key: str  # casting handle (== endpoint_name slug), e.g. "nemotron-3-nano-4b"
    provider: str  # provider key, e.g. "nvidia"
    app: str  # modal.App name, e.g. "nvidia-llms"
    endpoint_name: str  # URL slug
    served_model_id: str  # HF repo id vLLM serves (== ModelConfig.served_name)
    profile: str | None  # default tier this model is cast for, or None
    params_b: float | None  # total parameter count in billions


def entries() -> tuple[CatalogueEntry, ...]:
    """Every model as a flat engine-facing record (keyed by ``endpoint_name``)."""
    return tuple(
        CatalogueEntry(
            key=m.endpoint_name,
            provider=p.key,
            app=p.app,
            endpoint_name=m.endpoint_name,
            served_model_id=m.served_name,
            profile=m.profile,
            params_b=m.params_b,
        )
        for p in PROVIDERS.values()
        for m in p.models
    )


def litellm_model(served_model_id: str) -> str:
    """LiteLLM model string for an OpenAI-compatible custom endpoint."""
    return f"openai/{served_model_id}"


def endpoint_url(app: str, endpoint_name: str, workspace: str) -> str:
    """Public ``/v1`` URL Modal exposes for one endpoint in one workspace.

    Mirrors Modal's own naming: ``<workspace>--<app>-<endpoint_name>``. The
    workspace is the only deploy-specific part, so it is the lone argument the
    engine must supply from ``$MODAL_WORKSPACE``.
    """
    return f"https://{workspace}--{app}-{endpoint_name}.modal.run/v1"
