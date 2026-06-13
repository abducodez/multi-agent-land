"""Reusable, OpenAI-compatible model-serving layer for Modal.

This module is provider-agnostic. It takes a single ``ModelConfig`` and turns it
into a serverless, autoscaling, OpenAI-compatible HTTP endpoint backed by vLLM.
Each provider app (``app_nvidia.py``, ``app_openbmb.py``, ``app_google.py``)
imports :func:`register_all` and wires up its own models, so providers stay
isolated in their own Modal apps while sharing one serving path.

This is Modal's canonical vLLM recipe, kept deliberately small: an autoscaling
``@app.function`` whose body launches ``vllm serve`` as a subprocess behind a
``@modal.web_server``. Everything that shapes a model (GPU, context length,
parsers, multimodal limits, extra flags) lives in data — the ``ModelConfig`` —
not in code, so adding a model is one entry in ``catalogue.py``.

The served endpoints speak the OpenAI REST API (``/v1/chat/completions``,
``/v1/completions``, ``/v1/models``), so any OpenAI-compatible client can call
them by pointing ``base_url`` at the deployed URL.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable

import modal

# ModelConfig (and the whole model catalogue) lives in the stdlib-only
# ``catalogue`` module so the engine can read it without importing Modal. The
# serving layer here just consumes it.
from catalogue import ModelConfig

# --- Shared serving constants --------------------------------------------------

# Pin the inference stack so deploys are reproducible. Bump deliberately. This is
# the version Modal's current vLLM example ships with.
VLLM_VERSION = "0.21.0"
CUDA_IMAGE = "nvidia/cuda:12.9.0-devel-ubuntu22.04"
# Must match the local deploy environment's Python: every endpoint registers with
# `serialized=True`, and Modal requires a serialized function's image Python to
# match the version it was defined with (the repo's venv is 3.13).
PYTHON_VERSION = "3.13"

# The in-container port vLLM listens on; Modal maps it to a public HTTPS URL.
VLLM_PORT = 8000

# Cache paths inside the container, backed by shared Volumes (see below).
HF_CACHE_PATH = "/root/.cache/huggingface"
VLLM_CACHE_PATH = "/root/.cache/vllm"

# Name of the Modal Secret that holds a Hugging Face token (key: HF_TOKEN).
# Required only for gated repos. Create it once with:
#   modal secret create huggingface-secret HF_TOKEN=hf_...
HF_SECRET_NAME = "huggingface-secret"

# Name of the Modal Secret holding the bearer token clients must present. The key
# MUST be VLLM_API_KEY — vLLM reads that env var and then enforces
# `Authorization: Bearer <token>` on every request. Create it once with:
#   modal secret create llm-api-key VLLM_API_KEY=sk-...
API_KEY_SECRET_NAME = "llm-api-key"

# Opt in to API-key auth at deploy time (no code edits needed):
#   MODAL_LLM_REQUIRE_AUTH=1 modal deploy modal/app_google.py
# When enabled, every endpoint mounts API_KEY_SECRET_NAME and rejects requests
# without a valid bearer token. Off by default (endpoints are then public).
REQUIRE_API_KEY = os.environ.get("MODAL_LLM_REQUIRE_AUTH", "").lower() in ("1", "true", "yes")

# Demo-day switch: keep N containers warm for every *profile-bound* model (the
# tiers the cast actually runs on), removing their cold starts for the duration
# of the deploy. Specialists keep scale-to-zero. Costs GPU-hours while deployed —
# turn it on right before a live demo, redeploy without it after:
#   MODAL_LLM_KEEP_WARM=1 modal deploy modal/app_nvidia.py
KEEP_WARM = int(os.environ.get("MODAL_LLM_KEEP_WARM", "0") or "0")

# Weights and the vLLM compile cache are shared across every provider app, so a
# model pulled once is warm for all subsequent deploys and containers.
hf_cache_vol = modal.Volume.from_name("huggingface-cache", create_if_missing=True)
vllm_cache_vol = modal.Volume.from_name("vllm-cache", create_if_missing=True)

# Baseline image env shared by every model. Persisting the torch.compile + CUDA
# graph cache on the shared vLLM Volume means only the first container compiles;
# later cold starts replay the cached graphs instead of recapturing them.
_BASE_ENV = {
    "HF_HUB_CACHE": HF_CACHE_PATH,
    "HF_XET_HIGH_PERFORMANCE": "1",  # faster weight downloads
    "VLLM_LOG_STATS_INTERVAL": "1",
    "VLLM_CACHE_ROOT": VLLM_CACHE_PATH,
}


# --- Image + command construction ----------------------------------------------


def build_image(cfg: ModelConfig) -> modal.Image:
    """Build the container image for a model. Layers are cached and shared, so
    text models that only differ in env reuse the same base layers."""
    image = modal.Image.from_registry(CUDA_IMAGE, add_python=PYTHON_VERSION).entrypoint(
        []
    )  # drop the CUDA image's default entrypoint
    # vLLM version is per-model (defaults to the pinned VLLM_VERSION). A model can
    # opt into a nightly wheel when the pinned release can't serve its architecture.
    if cfg.vllm_version == "nightly":
        image = image.uv_pip_install("vllm", pre=True, extra_index_url="https://wheels.vllm.ai/nightly")
    else:
        image = image.uv_pip_install(f"vllm=={cfg.vllm_version or VLLM_VERSION}")
    image = image.env(_BASE_ENV)
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
    if cfg.gpu_memory_utilization is not None:
        cmd += ["--gpu-memory-utilization", str(cfg.gpu_memory_utilization)]
    # Prefix caching reuses the KV cache for shared prompt prefixes. In a
    # multi-agent cast the system prompt + shared ledger context repeat across
    # nearly every call, so this is one of the largest single wins here.
    cmd += ["--enable-prefix-caching"] if cfg.enable_prefix_caching else ["--no-enable-prefix-caching"]
    if cfg.async_scheduling:
        cmd += ["--async-scheduling"]
    if cfg.enforce_eager:
        cmd += ["--enforce-eager"]
    # Observability: log each incoming request (id, params, token counts) so the
    # Modal logs show what's actually being served.
    if cfg.log_requests:
        cmd += ["--enable-log-requests"]
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

    A single serialized ``@app.function`` web server launches ``vllm serve`` as a
    subprocess; Modal exposes its port at ``…--<app>-<endpoint_name>.modal.run``.
    Everything is serialized (the prebuilt ``vllm serve`` argv is shipped to the
    container), which lets us register many distinctly-named endpoints from a
    simple loop without each needing a hand-written module-level function.
    """
    image = build_image(cfg)
    cmd = build_command(cfg)
    secrets = []
    if cfg.gated:
        secrets.append(modal.Secret.from_name(HF_SECRET_NAME))
    if REQUIRE_API_KEY:
        # Exposes VLLM_API_KEY in the container; vLLM then enforces bearer auth.
        secrets.append(modal.Secret.from_name(API_KEY_SECRET_NAME))

    # Demo-day keep-warm: pin warm containers for the tier-bound models only —
    # specialists keep scale-to-zero (see KEEP_WARM above).
    min_containers = cfg.min_containers
    if KEEP_WARM and cfg.profile:
        min_containers = max(min_containers, KEEP_WARM)

    # Autoscale at ~75% of the ceiling, but let a hot container absorb a burst up
    # to the hard max before another cold-starts (Modal high-perf guidance).
    target_inputs = max(1, (cfg.max_concurrent_inputs * 3) // 4)

    @app.function(
        name=cfg.endpoint_name,
        image=image,
        gpu=cfg.gpu,
        volumes={HF_CACHE_PATH: hf_cache_vol, VLLM_CACHE_PATH: vllm_cache_vol},
        secrets=secrets,
        scaledown_window=cfg.scaledown_window,
        min_containers=min_containers,
        timeout=cfg.request_timeout,
        serialized=True,
    )
    @modal.concurrent(max_inputs=cfg.max_concurrent_inputs, target_inputs=target_inputs)
    @modal.web_server(port=VLLM_PORT, startup_timeout=cfg.startup_timeout)
    def serve():
        import subprocess

        # vLLM serves the OpenAI REST API on VLLM_PORT; Modal exposes it publicly.
        # Inherits the container env (HF cache, vLLM cache, any secrets).
        subprocess.Popen(cmd)

    return serve


def register_all(app: modal.App, configs: Iterable[ModelConfig]) -> None:
    """Register every model in ``configs`` onto ``app``."""
    for cfg in configs:
        register_model(app, cfg)
