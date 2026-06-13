"""Launch a local ``llama-server`` for a catalogue model — GPU when one is present.

This is the *serving* side of the llama.cpp backend (the read/binding side lives in
:mod:`src.models.llamacpp_catalogue`). It assembles the ``llama-server`` command for a
GGUF model, detects an accelerator (Apple Metal on macOS, NVIDIA CUDA elsewhere) and
offloads every layer to it when found, and otherwise serves on CPU — so the same command
works on a laptop and a GPU box. ``llama-server`` downloads the GGUF on first run via its
``-hf`` flag and exposes an OpenAI-compatible API on ``/v1``, which the engine reaches
through the same LiteLLM gateway as the Modal/HF backends.

Usage::

    # launch the tiny-tier Nemotron (GPU auto-detected), then export the URL it prints
    uv run python -m src.models.llamacpp_server nemotron-3-nano-4b
    export LLAMACPP_BASE_URL=http://127.0.0.1:8080/v1

    uv run python -m src.models.llamacpp_server --list          # show available models
    uv run python -m src.models.llamacpp_server minicpm-4-1-8b --cpu --print-only

``build_command`` and ``detect_accelerator`` are pure and take their inputs as arguments
(platform string, a probe callable) so tests can exercise the GPU/CPU branches without a
GPU or the ``llama-server`` binary present.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable

from src.models import llamacpp_catalogue
from src.models.llamacpp_catalogue import LlamaCppModel

# Default bind. 127.0.0.1 keeps the server local; pass --host 0.0.0.0 to expose it (e.g.
# a remote GPU box). The port matches llama-server's own default so the catalogue's
# DEFAULT_BASE_URL lines up with a no-flag launch.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_BINARY = "llama-server"

# Offload-all sentinel: llama.cpp treats a large -ngl as "every layer on the GPU".
_OFFLOAD_ALL_LAYERS = 999


def detect_accelerator(
    platform: str | None = None,
    probe: Callable[[], bool] | None = None,
) -> str:
    """Return the accelerator to offload to: ``"metal"``, ``"cuda"``, or ``"cpu"``.

    macOS (``darwin``) ships llama.cpp with Metal, so Apple Silicon always offloads.
    Elsewhere we offload only when an NVIDIA GPU is visible — probed with ``nvidia-smi``
    by default (injectable so tests don't depend on the host). Anything else → CPU.
    """
    plat = platform if platform is not None else sys.platform
    if plat == "darwin":
        return "metal"
    has_cuda = probe() if probe is not None else _nvidia_smi_present()
    return "cuda" if has_cuda else "cpu"


def _nvidia_smi_present() -> bool:
    """True when ``nvidia-smi`` exists and exits cleanly (a usable NVIDIA GPU)."""
    if shutil.which("nvidia-smi") is None:
        return False
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - host-dependent
        return False
    return result.returncode == 0


def gpu_layers(accelerator: str) -> int:
    """How many layers to offload for *accelerator*: all on a GPU, none on CPU."""
    return _OFFLOAD_ALL_LAYERS if accelerator in ("metal", "cuda") else 0


def build_command(
    model: LlamaCppModel,
    *,
    accelerator: str,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    ctx_size: int | None = None,
    binary: str = DEFAULT_BINARY,
) -> list[str]:
    """Assemble the ``llama-server`` argv for *model*. Returned as a list so the caller
    launches with ``subprocess`` and no shell (no quoting pitfalls).

    The model is pulled by its ``-hf`` spec (downloaded on first run) and served under
    ``--alias <key>`` so the running server reports the stable id the engine binds to.
    On a GPU (``metal``/``cuda``) every layer is offloaded (``-ngl 999``); on CPU the
    flag is omitted. Sampling defaults and flash-attention come from the model's
    catalogue entry; ``ctx_size`` overrides the per-model context window when given.
    """
    ctx = model.ctx_size if ctx_size is None else ctx_size
    cmd: list[str] = [
        binary,
        "-hf",
        model.hf_spec,
        "--alias",
        model.key,
        "--host",
        host,
        "--port",
        str(port),
        "--ctx-size",
        str(ctx),
        "--temp",
        str(model.temperature),
        "--top-p",
        str(model.top_p),
        "--top-k",
        str(model.top_k),
    ]
    layers = gpu_layers(accelerator)
    if layers:
        cmd += ["-ngl", str(layers)]
    if model.flash_attn:
        cmd += ["--flash-attn", "on"]
    return cmd


def base_url_for(host: str, port: int) -> str:
    """The OpenAI-compatible URL clients should use for a server bound to *host:port*.

    A server bound to ``0.0.0.0`` listens on every interface but is reached locally via
    the loopback address, so we advertise ``127.0.0.1`` in that case.
    """
    reachable = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"http://{reachable}:{port}/v1"


def _format_models() -> str:
    lines = ["Available llama.cpp models (key → repo · tier · params):"]
    for m in llamacpp_catalogue.LLAMACPP_MODELS:
        tier = m.profile or "—"
        params = f"{m.params_b:g}B" if m.params_b else "?"
        lines.append(f"  {m.key:<24} {m.hf_spec}  ·  {tier:<8} ·  {params}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.models.llamacpp_server",
        description="Launch a local llama-server for a catalogue model (GPU auto-detected).",
    )
    parser.add_argument("key", nargs="?", help="catalogue model key (see --list)")
    parser.add_argument("--list", action="store_true", help="list available models and exit")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"bind host (default {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"bind port (default {DEFAULT_PORT})")
    parser.add_argument("--ctx-size", type=int, default=None, help="override the model's context window")
    parser.add_argument("--cpu", action="store_true", help="force CPU (skip GPU offload)")
    parser.add_argument("--binary", default=DEFAULT_BINARY, help=f"llama-server binary (default {DEFAULT_BINARY})")
    parser.add_argument("--print-only", action="store_true", help="print the command and export line, do not launch")
    args = parser.parse_args(argv)

    if args.list or not args.key:
        print(_format_models())
        return 0 if args.list else 2

    model = llamacpp_catalogue.model_by_key(args.key)
    if model is None:
        print(f"unknown model {args.key!r}.\n\n{_format_models()}", file=sys.stderr)
        return 2

    accelerator = "cpu" if args.cpu else detect_accelerator()
    cmd = build_command(
        model,
        accelerator=accelerator,
        host=args.host,
        port=args.port,
        ctx_size=args.ctx_size,
        binary=args.binary,
    )
    url = base_url_for(args.host, args.port)

    where = {"metal": "Apple Metal GPU", "cuda": "NVIDIA GPU", "cpu": "CPU"}[accelerator]
    print(f"▶ {model.key} ({model.hf_spec}) on {where}")
    print(f"  {' '.join(cmd)}")
    print(f"\nPoint the engine at it:\n  export {llamacpp_catalogue._BASE_URL_ENV}={url}\n")

    if args.print_only:
        return 0

    if shutil.which(args.binary) is None:
        print(
            f"'{args.binary}' not found on PATH. Install llama.cpp "
            "(https://github.com/ggml-org/llama.cpp) or pass --binary /path/to/llama-server.",
            file=sys.stderr,
        )
        return 127

    # Export the URL into this process's children too, so a wrapper that launches the
    # app in the same shell session sees it; the printed line covers the manual case.
    os.environ[llamacpp_catalogue._BASE_URL_ENV] = url
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
