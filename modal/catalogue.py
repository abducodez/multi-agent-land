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

    # Inference shape
    max_model_len: int | None = None  # cap context to fit memory / task
    trust_remote_code: bool = False  # required by MiniCPM / Nemotron custom code

    # OpenAI feature parsers (vLLM names; leave None if unsupported on the model)
    reasoning_parser: str | None = None
    tool_call_parser: str | None = None
    enable_auto_tool_choice: bool = False

    # Multimodal
    multimodal: bool = False
    mm_limits: dict[str, int] | None = None  # e.g. {"image": 4, "audio": 2}

    # Scaling / lifecycle
    max_concurrent_inputs: int = 64  # requests multiplexed onto one container
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
    ),
    ModelConfig(
        name="nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16",
        endpoint_name="nemotron-3-nano-30b",
        # 30B total params in BF16 (~60GB) though only ~3B activate per token.
        # An alternate strong model — not cast to a profile by default.
        params_b=30,
        gpu="H200:1",
        max_model_len=32768,
        trust_remote_code=True,
        gated=True,
        max_concurrent_inputs=64,
    ),
    ModelConfig(
        name="nvidia/Nemotron-Cascade-14B-Thinking",
        endpoint_name="nemotron-cascade-14b-thinking",
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
    ),
)

# --- Google (Gemma) ------------------------------------------------------------

GOOGLE_MODELS: tuple[ModelConfig, ...] = (
    ModelConfig(
        name="google/gemma-4-12B",
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
