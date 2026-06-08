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
from collections.abc import Iterable

import modal

# ModelConfig (and the whole model catalogue) lives in the stdlib-only
# ``catalogue`` module so the engine can read it without importing Modal. The
# serving layer here just consumes it.
from catalogue import ModelConfig

# --- Shared serving constants --------------------------------------------------

# Pin the inference stack so deploys are reproducible. Bump deliberately.
VLLM_VERSION = "0.21.0"
CUDA_IMAGE = "nvidia/cuda:12.9.0-devel-ubuntu22.04"
PYTHON_VERSION = "3.13"

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

# Emit logs as structured JSON (one object per line) instead of vLLM's default
# human-readable text. Opt in at deploy time (no code edits), mirroring the auth
# toggle above:
#   MODAL_LLM_JSON_LOGS=1 modal deploy modal/app_google.py
# Off by default — the coloured text logs are nicer to watch live; turn this on
# when shipping logs to an aggregator or grepping fields. Request-level logging
# itself (the per-request detail) is always on via ModelConfig, independent of
# the format chosen here.
JSON_LOGS = os.environ.get("MODAL_LLM_JSON_LOGS", "").lower() in ("1", "true", "yes")

# Verbosity for the served loggers (vLLM honours VLLM_LOGGING_LEVEL; the JSON
# config applies the same level). Read at deploy time and baked into the image.
LOG_LEVEL = os.environ.get("MODAL_LLM_LOG_LEVEL", "INFO").upper()

# Where the structured-logging module + its generated config live in the
# container. The module dir goes on PYTHONPATH so vLLM can import the formatter
# the dictConfig references (``vllm_logging.JsonFormatter``).
_LOG_MODULE_DIR = "/opt/mal_logging"
_LOG_CONFIG_PATH = "/tmp/vllm_logging.json"

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
    # Verbosity of vLLM's own loggers (throughput/cache stats, request logs).
    "VLLM_LOGGING_LEVEL": LOG_LEVEL,
    # Persist torch.compile + CUDA-graph artifacts on the shared vLLM cache
    # Volume (mounted at VLLM_CACHE_PATH). The first container compiles; every
    # later cold start replays the cached graphs instead of recompiling, so we
    # keep CUDA graphs (throughput) without paying their capture cost each boot.
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
    if JSON_LOGS:
        # Ship the stdlib JSON formatter and put it on PYTHONPATH so vLLM can
        # import it when it applies the dictConfig. ``serve()`` writes the config
        # file and points VLLM_LOGGING_CONFIG_PATH at it. Baking the toggle into
        # the image env is what lets the (deploy-time) flag reach the container.
        from pathlib import Path

        image = (
            image.add_local_file(
                Path(__file__).with_name("vllm_logging.py"),
                f"{_LOG_MODULE_DIR}/vllm_logging.py",
                copy=True,
            )
            .env({"PYTHONPATH": _LOG_MODULE_DIR})
            .env({"MODAL_LLM_JSON_LOGS": "1", "MODAL_LLM_LOG_LEVEL": LOG_LEVEL})
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
    # Performance / throughput knobs (all data-driven from ModelConfig).
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
    if cfg.max_num_seqs:
        cmd += ["--max-num-seqs", str(cfg.max_num_seqs)]
    if cfg.max_num_batched_tokens:
        cmd += ["--max-num-batched-tokens", str(cfg.max_num_batched_tokens)]
    # Observability: log each incoming request (id, params, token counts) so the
    # Modal logs show what's actually being served. Bound the logged prompt length
    # by default so a long context can't blow up the log line.
    if cfg.log_requests:
        cmd += ["--enable-log-requests"]
    if cfg.log_outputs:
        cmd += ["--enable-log-outputs"]
    if cfg.max_log_len is not None:
        cmd += ["--max-log-len", str(cfg.max_log_len)]
    if not cfg.uvicorn_access_log:
        cmd += ["--disable-uvicorn-access-log"]
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

    # Autoscale at the target, but let a hot container absorb a burst up to the
    # hard max before another cold-starts (Modal high-perf-inference guidance).
    # Default the target to ~75% of the ceiling so we scale out before saturating.
    target_inputs = cfg.target_concurrent_inputs or max(1, (cfg.max_concurrent_inputs * 3) // 4)

    function_kwargs = dict(
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
    # Pre-warm spare containers under load for bursty traffic (opt-in per model).
    if cfg.buffer_containers:
        function_kwargs["buffer_containers"] = cfg.buffer_containers

    @app.function(**function_kwargs)
    @modal.concurrent(max_inputs=cfg.max_concurrent_inputs, target_inputs=target_inputs)
    @modal.web_server(port=VLLM_PORT, startup_timeout=cfg.startup_timeout)
    def serve():
        import os
        import subprocess

        env = dict(os.environ)
        # When structured logging is on, generate the dictConfig file and point
        # vLLM at it. Done at container start (not build) so the level is picked
        # up from the env without rebuilding the image.
        if env.get("MODAL_LLM_JSON_LOGS", "").lower() in ("1", "true", "yes"):
            import vllm_logging

            vllm_logging.write_config(_LOG_CONFIG_PATH, level=env.get("MODAL_LLM_LOG_LEVEL", "INFO"))
            env["VLLM_LOGGING_CONFIG_PATH"] = _LOG_CONFIG_PATH

        # vLLM serves the OpenAI REST API on VLLM_PORT; Modal exposes it publicly.
        subprocess.Popen(cmd, env=env)

    return serve


def register_all(app: modal.App, configs: Iterable[ModelConfig]) -> None:
    """Register every model in ``configs`` onto ``app``."""
    for cfg in configs:
        register_model(app, cfg)
