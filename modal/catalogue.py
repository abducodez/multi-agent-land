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

    # Performance / throughput (vLLM serve flags). Defaults target high
    # steady-state throughput on the common single-GPU path; tune per model.
    # See ``service.build_command`` for how each maps to a flag. For anything more
    # exotic (quantization, batch-size caps, …) use ``extra_vllm_args``.
    gpu_memory_utilization: float | None = None  # fraction of VRAM for weights + KV cache (vLLM default 0.9)
    enable_prefix_caching: bool = True  # reuse KV for shared prompt prefixes — big win when system/context repeat
    async_scheduling: bool = True  # overlap CPU request scheduling with GPU compute
    enforce_eager: bool = False  # skip CUDA-graph capture: faster cold start, lower steady-state throughput

    # Observability. ``log_requests`` adds --enable-log-requests so each call's id,
    # sampling params, and token counts show in the Modal container logs.
    log_requests: bool = True

    # OpenAI feature parsers (vLLM names; leave None if unsupported on the model)
    reasoning_parser: str | None = None
    tool_call_parser: str | None = None
    enable_auto_tool_choice: bool = False

    # Multimodal — per-prompt input caps, e.g. {"image": 4, "audio": 2}. Set the
    # caps to 0 on an auto-detected-multimodal model you serve text-only, to skip
    # the encoder warmup and free memory.
    mm_limits: dict[str, int] | None = None

    # Scaling / lifecycle
    max_concurrent_inputs: int = 64  # hard ceiling of requests multiplexed onto one container
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
        # Tiny Titan tier (≤4B): ~4B BF16 weights (~8GB) fit a single 24GB L4.
        profile="tiny",
        params_b=4,
        gpu="L4:1",
        max_model_len=16384,
        # Hybrid Mamba-2 + MLP + attention arch → custom modeling code; required.
        trust_remote_code=True,
        gated=True,
        max_concurrent_inputs=32,
        # Served as a plain chat endpoint. NVIDIA ships a custom `nano_v3` reasoning
        # parser as a downloadable plugin file (--reasoning-parser-plugin) plus a
        # `qwen3_coder` tool parser; both are omitted here for boot-robustness (the
        # plugin must be shipped into the image and is easy to get wrong). The
        # model still reasons — the <think> block just stays inline in the content.
        # Add them later via extra_vllm_args if structured reasoning/tools are needed.
    ),
    # NOTE: nemotron-3-nano-30b (NVIDIA-Nemotron-3-Nano-30B-A3B-BF16, ~31B/A3B on an
    # A100) was removed to stay within the workspace's 8 Web-Function cap — it was an
    # unbound specialist (no tier, unreferenced by the engine/config), so dropping it
    # costs the live cast nothing. Re-add a ModelConfig here (and free a slot, or lift
    # the plan cap) to bring it back. See modal/README.md.
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
        # Post-trained from Qwen3-14B Base → stock Qwen3 arch (no custom code).
        # ChatML thinking block parsed by the Qwen3 reasoning parser; `hermes` is
        # the standard Qwen3-family tool parser. Both verified built-in in vLLM.
        reasoning_parser="qwen3",
        tool_call_parser="hermes",
        enable_auto_tool_choice=True,
        max_concurrent_inputs=48,
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
        # No tool_call_parser on purpose: MiniCPM4.1 emits a custom
        # <|tool_call_start|> code-block format vLLM has no matching parser for, so
        # a tool parser would 400/mis-parse. The engine's structured path uses vLLM
        # guided decoding (response_format json_schema) instead, which is
        # parser-independent — see ADR-0016. Don't bolt on a mismatched parser.
        # (The model card suggests a vLLM nightly; 0.21.0 predates the release and
        # serves it fine — flip vllm_version="nightly" if a boot failure proves otherwise.)
    ),
    ModelConfig(
        name="openbmb/MiniCPM-o-4_5",
        endpoint_name="minicpm-o-4-5",
        # Omni-modal (text + vision + audio) on a Qwen3-8B backbone → ~9B total in
        # BF16. A specialist model, not cast to a profile by default.
        params_b=9,
        gpu="L40S:1",
        trust_remote_code=True,
        # Text + image only here; audio in/out over vLLM is experimental (it really
        # wants the Transformers/demo runtime). Caps keep the encoder warmup bounded.
        mm_limits={"image": 1, "audio": 0, "video": 0},
        # Light vision/audio preprocessing backends. NOTE: full omni support wants
        # openbmb's `minicpmo-utils[all]` + a pinned transformers==4.51.0, but that
        # pin conflicts with vLLM's bundled transformers — so we keep the lean set
        # and serve text+image. Treat audio as experimental.
        extra_pip=("librosa", "soundfile", "timm"),
        gpu_memory_utilization=0.9,
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
        # gemma4_unified (encoder-free) has no native class in any *stable* vLLM
        # (≤0.22.1 falls back to the Transformers backend and crashes); only the
        # nightly wheel registers Gemma4UnifiedForConditionalGeneration. So this
        # model alone pins the nightly + transformers>=5.10.2. Scoped here, so
        # NVIDIA/OpenBMB and the 26B sibling stay on the reproducible pin.
        vllm_version="nightly",
        extra_pip=("transformers>=5.10.2",),
        # Transformers-backend / fresh-nightly path: eager-only is the safe choice
        # (CUDA-graph capture + async scheduler aren't reliable here).
        enforce_eager=True,
        async_scheduling=False,
        # Text-only in the cast — gemma4 auto-detects as multimodal, so zero the
        # per-prompt caps to skip the encoder warmup and free memory for KV cache.
        mm_limits={"image": 0, "audio": 0},
    ),
    ModelConfig(
        name="google/gemma-4-26B-A4B-it",
        endpoint_name="gemma-4-26b",
        # MoE: ~25B total params (~4B active) with a small vision encoder. Gated.
        profile="strong",
        params_b=26,
        gpu="A100",
        max_model_len=32768,
        gated=True,
        reasoning_parser="gemma4",
        tool_call_parser="gemma4",
        enable_auto_tool_choice=True,
        max_concurrent_inputs=64,
        # Standard gemma4 MoE arch (NOT the unified 12B path): served by a native
        # vLLM class on the pinned stable release (0.19.1+), so NO nightly, no
        # transformers pin, and CUDA graphs + async scheduling work — defaults stand.
        # Text-only in the cast: zero the auto-detected multimodal caps.
        mm_limits={"image": 0},
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
