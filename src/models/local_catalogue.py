"""Local in-process inference catalogue — the third backend, next to Modal and HF.

Where ``modal/catalogue.py`` describes models the project deploys itself (vLLM on
Modal GPUs) and ``hf_catalogue.py`` describes models reachable on Hugging Face's
serverless router, this module describes small **transformers** models served
**in-process on the host's own GPU** through a ``@spaces.GPU`` function (see ADR-0033).

It is hardware-agnostic by design. The ``@spaces.GPU`` decorator the provider uses is
**effect-free off ZeroGPU**, so one code path covers every HF Space hardware flavour:

  * **ZeroGPU** — a GPU is granted only for the duration of each ``@spaces.GPU`` call
    and released after; the decorator does the dynamic allocation. Subject to a daily
    GPU quota (~5 min free), so a live show should stay small.
  * **Dedicated GPU** (T4 / L4 / L40S / A100 / …) or a **local CUDA box** — the
    decorator is a passthrough and the model runs on the persistent GPU; no per-call
    allocation, no quota (you pay for the GPU by the hour instead).

This replaces the earlier llama.cpp backend (ADR-0032, superseded): llama.cpp serves
from a *persistent* ``llama-server`` process that holds the GPU, which ZeroGPU cannot
give it. The in-process transformers path needs no server and works on either hardware.

Unlike the Modal/HF backends, this one does **not** route through the LiteLLM HTTP
gateway — there is no endpoint to call. ``binding_for`` returns the bare ``transformers``
``repo_id`` as ``model`` (no ``openai/`` prefix, empty ``base_url``); the router sees the
``local`` backend tag and builds a
:class:`~src.models.local_provider.LocalTransformersProvider` instead of the HTTP provider.

Like its sibling catalogues this file is **stdlib-only** and reaches no network: pure
data plus string building, read offline by the engine and the Lab picker. Add a model =
append one :class:`LocalModel`. Every model stays within the ≤32B "small minds" rule;
the ``tiny`` default honours the Tiny-Titan ≤4B band.

**Quota note (ZeroGPU only).** Free ZeroGPU grants ~5 minutes of GPU/day (2 for anonymous
visitors), billed per ``@spaces.GPU`` call. A full multi-agent show makes many sequential
calls, so the catalogue deliberately tags **one tiny model** as the only tier default:
with no per-tier override the whole cast routes to it (see ``lab._default_model_key``),
keeping a live show inside the daily budget. On a dedicated GPU there is no such cap, but
the tiny default still keeps first-token latency low. Larger alternates are listed
untagged — a cast can pin them, but they are never the silent default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

# Env signals that a GPU is reachable for the in-process path:
#   * SPACES_ZERO_GPU — set by HF on ZeroGPU hardware (a GPU is obtainable inside
#     ``@spaces.GPU``); CUDA is only emulated outside the decorator, so we trust the env
#     rather than probing torch there.
#   * LOCAL_INFERENCE — explicit operator opt-in for a dedicated-GPU Space or a CUDA box
#     where no HF env var advertises the GPU; also the deterministic switch tests use.
# When neither is set we fall back to a cached ``torch.cuda.is_available()`` probe so a
# plain GPU Space "just works" without any configuration.
_ZERO_GPU_ENV = "SPACES_ZERO_GPU"
_LOCAL_OVERRIDE_ENV = "LOCAL_INFERENCE"


@dataclass(frozen=True)
class LocalModel:
    """One small ``transformers`` model servable in-process on the host's GPU.

    ``repo_id`` is the Hugging Face repo (also the id the provider loads with
    ``transformers``). ``profile`` is the tier this model is the default casting for, or
    None for an alternate the cast can still pin explicitly. ``source`` is a friendly
    family/org label for the picker. ``trust_remote_code`` is forwarded to
    ``from_pretrained`` for repos that ship custom modelling code (e.g. MiniCPM).
    """

    repo_id: str
    profile: str | None = None
    params_b: float | None = None
    source: str = "Hugging Face"
    trust_remote_code: bool = False

    @property
    def key(self) -> str:
        """Catalogue key (the repo id; the backend registry namespaces it ``local:<key>``)."""
        return self.repo_id

    @property
    def served_model_id(self) -> str:
        return self.repo_id


# --- The catalogue: small transformers instruct models -------------------------------
# One tiny model is tagged as the cast-wide default (low latency + ZeroGPU-quota-friendly,
# see the module docstring); the rest are untagged alternates a cast can pin. Plain data:
# swapping the default or adding a sponsor family is a one-line edit.

LOCAL_MODELS: tuple[LocalModel, ...] = (
    # Tiny tier (≤4B, Tiny-Titan band) — the cast-wide default. Small, fast, and a
    # reliable chat template, so a full show stays low-latency (and well inside the free
    # ZeroGPU GPU/day budget).
    LocalModel(
        repo_id="Qwen/Qwen2.5-3B-Instruct",
        profile="tiny",
        params_b=3.0,
        source="Qwen",
    ),
    # OpenBMB MiniCPM 4.1 8B (fast tier) — keeps the OpenBMB lane on the in-process path.
    # Ships custom modelling code, so trust_remote_code is required. An alternate, not the
    # default: a cast can pin it, but the tiny model above drives a show by default.
    LocalModel(
        repo_id="openbmb/MiniCPM4.1-8B",
        params_b=8.0,
        source="OpenBMB MiniCPM",
        trust_remote_code=True,
    ),
    # Qwen 7B (fast tier) — a slightly larger alternate for a single specialist seat (e.g.
    # the Judge) when the hardware/quota allows. Untagged, so never the silent default.
    LocalModel(
        repo_id="Qwen/Qwen2.5-7B-Instruct",
        params_b=7.0,
        source="Qwen",
    ),
)


# --- engine-facing read view (mirrors modal_catalogue / hf_catalogue dict shape) ------


def _build_entry(m: LocalModel) -> dict:
    """One model as a plain dict, shaped like ``modal_catalogue.entries()``."""
    return {
        "key": m.key,
        "provider": m.source,
        "app": "local",
        "endpoint_name": m.repo_id,
        "served_model_id": m.served_model_id,
        "profile": m.profile,
        "params_b": m.params_b,
    }


# Built once at import (the catalogue is static): callers that mutate copy first.
_ENTRIES: tuple[dict, ...] = tuple(_build_entry(m) for m in LOCAL_MODELS)
_ENTRY_BY_KEY: dict[str, dict] = {e["key"]: e for e in _ENTRIES}
_MODEL_BY_KEY: dict[str, LocalModel] = {m.key: m for m in LOCAL_MODELS}


def entries() -> list[dict]:
    """Every local model as a plain dict, shaped like the other catalogues:

    ``{key, provider, app, endpoint_name, served_model_id, profile, params_b}`` — so the
    unified registry and the Lab picker treat all three backends identically.
    """
    return list(_ENTRIES)


def entry_by_key(key: str) -> dict | None:
    """The catalogue entry whose key (the repo id) is *key*, or None."""
    return _ENTRY_BY_KEY.get(key)


def model_by_key(key: str) -> LocalModel | None:
    """The full :class:`LocalModel` for *key* (loader fields included), or None.

    The provider uses this to read ``trust_remote_code``; the engine path needs only
    :func:`binding_for`.
    """
    return _MODEL_BY_KEY.get(key)


def default_key_for_profile(profile: str) -> str | None:
    """The key of the model tagged for *profile* (first match), or None.

    Only the tiny model is tagged, so every other tier returns None and the Lab falls
    back to the first catalogue entry — i.e. the whole cast routes to the tiny model
    unless a seat is pinned to an alternate. That fallback is the latency/quota guardrail.
    """
    return next((m.key for m in LOCAL_MODELS if m.profile == profile), None)


def _truthy(value: str) -> bool:
    """Accept the usual on-ish spellings HF / shells use for a boolean env var."""
    return value.strip().lower() in ("1", "true", "yes", "on")


def _cuda_available() -> bool:
    """Cached ``torch.cuda.is_available()`` — the auto-detect fallback for the gate.

    Lets a dedicated-GPU Space (or a local CUDA box) go live with no configuration. Torch
    is imported lazily and every failure mode (not installed, no CUDA, a driver hiccup)
    degrades to ``False`` so the offline stub stays the default. Cached so the heavy
    import happens at most once.
    """
    global _CUDA_CACHE
    if _CUDA_CACHE is None:
        try:
            import torch

            _CUDA_CACHE = bool(torch.cuda.is_available())
        except Exception:  # pragma: no cover - torch absent / driver error → not live
            _CUDA_CACHE = False
    return _CUDA_CACHE


_CUDA_CACHE: bool | None = None


def has_credentials(env: dict[str, str] | None = None, *, cuda_probe: Callable[[], bool] | None = None) -> bool:
    """True when the local in-process backend can actually obtain a GPU here.

    There is no token to gate on — running a ``transformers`` model in-process needs a
    reachable GPU, which means a ZeroGPU Space (HF sets ``SPACES_ZERO_GPU``), an explicit
    operator opt-in (``LOCAL_INFERENCE``), or a CUDA device the auto-detect probe finds
    (a dedicated-GPU Space or a local box). Gating on capability keeps the offline stub
    the default on a CPU-only host, so a laptop demo stays reproducible. Selecting this
    backend in the Lab is the per-run opt-in; this is the per-host "is it even possible"
    gate the live chip reads.

    ``cuda_probe`` is injectable so tests can drive the auto-detect branch deterministically
    without a GPU; production uses the cached :func:`_cuda_available`.

    The torch auto-probe runs **only against the real process environment** (``env`` is
    None or ``os.environ`` itself). With an explicit ``env`` dict — the way tests and the
    façade's hypothetical checks call it — the two env signals above are the whole story,
    so the gate stays deterministic and never imports torch on a non-GPU host.
    """
    source = os.environ if env is None else env
    if _truthy(source.get(_ZERO_GPU_ENV, "")) or _truthy(source.get(_LOCAL_OVERRIDE_ENV, "")):
        return True
    if cuda_probe is not None:
        return bool(cuda_probe())
    if env is None or env is os.environ:
        return _cuda_available()
    return False


def binding_for(key: str, env: dict[str, str] | None = None) -> dict:
    """Resolve a catalogue *key* into a concrete in-process binding.

    Returns ``{"model", "base_url", "api_key"}`` where ``model`` is the **bare**
    ``transformers`` repo id (no ``openai/`` prefix — this backend is not called over
    HTTP), and ``base_url`` / ``api_key`` are empty (there is no endpoint and no auth).
    The router recognises the ``local`` backend tag and builds a
    :class:`~src.models.local_provider.LocalTransformersProvider` from ``model``. Raises
    ``KeyError`` for an unknown key.
    """
    model = _MODEL_BY_KEY.get(key)
    if model is None:
        known = sorted(_MODEL_BY_KEY)
        raise KeyError(f"unknown local model {key!r}; known: {known}")
    return {
        "model": model.served_model_id,
        "base_url": "",
        "api_key": "",
    }
