"""Reusable, OpenAI-compatible model-serving layer for Modal.

This module is provider-agnostic. It knows how to take a single ``ModelConfig``
and turn it into a serverless, autoscaling, OpenAI-compatible HTTP endpoint
backed by vLLM. Each provider app (``app_nvidia.py``, ``app_openbmb.py``,
``app_google.py``) imports :func:`register_model` and wires up its own models,
so providers stay fully isolated in their own Modal apps while sharing one
battle-tested serving path.

Design goals:
- **Extensible**: add a model by appending one ``ModelConfig`` to the registry.
- **Scalable**: serverless autoscaling, input concurrency, shared weight cache.
- **Configurable per task**: every knob (GPU, context length, parsers,
  multimodal limits, extra flags) lives in data, not code.

The served endpoints speak the OpenAI REST API (``/v1/chat/completions`,
``/v1/completions``, ``/v1/models``), so any OpenAI-compatible client can call
them by pointing ``base_url`` at the deployed URL.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import modal

# --- Shared serving constants --------------------------------------------------

# Pin the inference stack so deploys are reproducible. Bump deliberately.
VLLM_VERSION = "0.21.0"
CUDA_IMAGE = "nvidia/cuda:12.9.0-devel-ubuntu22.04"
PYTHON_VERSION = "3.12"

# The in-container port vLLM listens on; Modal maps it to a public HTTPS URL.
VLLM_PORT = 8000

# Cache paths inside the container, backed by shared Volumes (see below).
HF_CACHE_PATH = "/root/.cache/huggingface"
VLLM_CACHE_PATH = "/root/.cache/vllm"

# Name of the Modal Secret that holds a Hugging Face token (key: HF_TOKEN).
# Required only for gated repos (e.g. Gemma). Create it once with:
#   modal secret create huggingface-secret HF_TOKEN=hf_...
HF_SECRET_NAME = "huggingface-secret"

# Name of the Modal Secret holding the bearer token clients must present.
# The key MUST be VLLM_API_KEY — vLLM reads that env var and then enforces
# `Authorization: Bearer <token>` on every request. Create it once with:
#   modal secret create llm-api-key VLLM_API_KEY=sk-...
API_KEY_SECRET_NAME = "llm-api-key"

# Opt in to API-key auth at deploy time (no code edits needed):
#   MODAL_LLM_REQUIRE_AUTH=1 modal deploy modal/app_google.py
# When enabled, every endpoint mounts API_KEY_SECRET_NAME and rejects requests
# without a valid bearer token. Off by default (endpoints are then public).
REQUIRE_API_KEY = os.environ.get("MODAL_LLM_REQUIRE_AUTH", "").lower() in (
    "1",
    "true",
    "yes",
)

# Weights and the vLLM compile cache are shared across every provider app, so a
# model pulled once is warm for all subsequent deploys and containers.
hf_cache_vol = modal.Volume.from_name("huggingface-cache", create_if_missing=True)
vllm_cache_vol = modal.Volume.from_name("vllm-cache", create_if_missing=True)

# Baseline image shared by every text model. Multimodal models extend it via
# ``ModelConfig.extra_pip`` (see ``build_image``).
_BASE_ENV = {
    "HF_HUB_CACHE": HF_CACHE_PATH,
    "HF_XET_HIGH_PERFORMANCE": "1",  # faster weight downloads
    "VLLM_LOG_STATS_INTERVAL": "1",
}


# --- Model configuration -------------------------------------------------------


@dataclass(frozen=True)
class ModelConfig:
    """Everything needed to serve one model as an OpenAI-compatible endpoint.

    Add a new model by constructing one of these in ``registry.py``. Nothing
    else needs to change.
    """

    # Identity
    name: str  # Hugging Face repo id, e.g. "google/gemma-4-12B"
    endpoint_name: str  # Modal function + URL slug, e.g. "gemma-4-12b"
    served_model_name: str | None = None  # model id clients pass; defaults to `name`
    revision: str | None = None  # pin a commit for reproducibility

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


# --- Image + command construction ----------------------------------------------


def build_image(cfg: ModelConfig) -> modal.Image:
    """Build the container image for a model. Layers are cached and shared, so
    text models that only differ in env reuse the same base layers."""
    image = (
        modal.Image.from_registry(CUDA_IMAGE, add_python=PYTHON_VERSION)
        .entrypoint([])  # drop the CUDA image's default entrypoint
        .uv_pip_install(f"vllm=={VLLM_VERSION}")
        .env(_BASE_ENV)
    )
    if cfg.extra_pip:
        image = image.uv_pip_install(*cfg.extra_pip)
    if cfg.env:
        image = image.env(cfg.env)
    return image


def build_command(cfg: ModelConfig) -> list[str]:
    """Assemble the ``vllm serve`` argv for a model. Returned as a list so we can
    launch with ``subprocess.Popen`` without a shell (no quoting pitfalls)."""
    cmd: list[str] = [
        "vllm",
        "serve",
        cfg.name,
        "--host",
        "0.0.0.0",
        "--port",
        str(VLLM_PORT),
        "--served-model-name",
        cfg.served_name,
        "--tensor-parallel-size",
        str(cfg.tensor_parallel_size),
        "--uvicorn-log-level",
        "info",
    ]
    if cfg.revision:
        cmd += ["--revision", cfg.revision]
    if cfg.max_model_len:
        cmd += ["--max-model-len", str(cfg.max_model_len)]
    if cfg.trust_remote_code:
        cmd += ["--trust-remote-code"]
    if cfg.reasoning_parser:
        cmd += ["--reasoning-parser", cfg.reasoning_parser]
    if cfg.enable_auto_tool_choice:
        cmd += ["--enable-auto-tool-choice"]
    if cfg.tool_call_parser:
        cmd += ["--tool-call-parser", cfg.tool_call_parser]
    if cfg.mm_limits:
        cmd += ["--limit-mm-per-prompt", json.dumps(cfg.mm_limits)]
    cmd += list(cfg.extra_vllm_args)
    return cmd


# --- Endpoint registration ------------------------------------------------------


def register_model(app: modal.App, cfg: ModelConfig) -> modal.Function:
    """Attach one model to ``app`` as an autoscaling, OpenAI-compatible endpoint.

    The function is serialized (its prebuilt ``vllm serve`` argv is shipped to
    the container), which lets us register many distinctly-named endpoints from
    a simple loop without each needing a hand-written module-level function.
    """
    image = build_image(cfg)
    cmd = build_command(cfg)
    secrets = []
    if cfg.gated:
        secrets.append(modal.Secret.from_name(HF_SECRET_NAME))
    if REQUIRE_API_KEY:
        # Exposes VLLM_API_KEY in the container; vLLM then enforces bearer auth.
        secrets.append(modal.Secret.from_name(API_KEY_SECRET_NAME))

    @app.function(
        name=cfg.endpoint_name,
        image=image,
        gpu=cfg.gpu,
        volumes={HF_CACHE_PATH: hf_cache_vol, VLLM_CACHE_PATH: vllm_cache_vol},
        secrets=secrets,
        scaledown_window=cfg.scaledown_window,
        min_containers=cfg.min_containers,
        timeout=cfg.request_timeout,
        serialized=True,
    )
    @modal.concurrent(max_inputs=cfg.max_concurrent_inputs)
    @modal.web_server(port=VLLM_PORT, startup_timeout=cfg.startup_timeout)
    def serve():
        import subprocess

        # vLLM serves the OpenAI REST API on VLLM_PORT; Modal exposes it publicly.
        subprocess.Popen(cmd)

    return serve


def register_all(app: modal.App, configs: list[ModelConfig]) -> None:
    """Register every model in ``configs`` onto ``app``."""
    for cfg in configs:
        register_model(app, cfg)
