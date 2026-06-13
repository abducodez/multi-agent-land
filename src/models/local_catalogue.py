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
visitors), billed per ``@spaces.GPU`` call. Each tier maps to a *different* sponsor model
(see ``LOCAL_MODELS``), so a cross-sponsor cast loads several multi-GB models per show —
heavy on that daily budget and on host RAM. A dedicated-GPU Space has no such cap; for a
quota-light demo, pin the whole cast to the tiny default in the Lab (one model, low
latency). The tiny model is listed first, so any untagged fallback (see
``lab._default_model_key``) also lands on the cheapest tier.
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
    ``from_pretrained`` for repos that ship custom modelling code (e.g. MiniCPM, Nemotron).
    ``auto_class`` is the ``transformers`` auto-class the provider loads the repo with —
    ``AutoModelForCausalLM`` for an ordinary LM, overridden where a model card calls for a
    different one (e.g. JetBrains Mellum loads with ``AutoModelForMultimodalLM``).
    """

    repo_id: str
    profile: str | None = None
    params_b: float | None = None
    source: str = "Hugging Face"
    trust_remote_code: bool = False
    auto_class: str = "AutoModelForCausalLM"

    @property
    def key(self) -> str:
        """Catalogue key (the repo id; the backend registry namespaces it ``local:<key>``)."""
        return self.repo_id

    @property
    def served_model_id(self) -> str:
        return self.repo_id


# --- The catalogue: one sponsor model per tier ---------------------------------------
# Each tier is tagged with a distinct sponsor family, so a single cast legitimately spans
# four sponsors at once (NVIDIA · OpenBMB · Cohere · JetBrains) — the multi-track prize
# strategy run on the Space's own GPU, no endpoint to deploy. Every model honours the ≤32B
# "small minds" rule and the tiny default keeps the Tiny-Titan ≤4B band. Plain data:
# swapping a tier's model is a one-line edit.
#
# ZeroGPU cost: a cross-sponsor cast loads several multi-GB models per show (a download on
# first use, then a host→device copy per turn), which is heavy on the free ~5-min/day GPU
# quota and on host RAM. A dedicated-GPU Space has no such cap; for a quota-light demo, pin
# the whole cast to the tiny default in the Lab. The first entry is the tiny default, so any
# untagged fallback also lands on the cheapest model.

LOCAL_MODELS: tuple[LocalModel, ...] = (
    # Tiny tier (≤4B, Tiny-Titan band) — the cast-wide fallback default. NVIDIA Nemotron
    # Nano is a Mamba-2/Transformer hybrid; load the BF16 (safetensors) sibling, not the
    # GGUF, since the in-process path runs transformers. Ships custom modelling code, so
    # trust_remote_code is required.
    LocalModel(
        repo_id="nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16",
        profile="tiny",
        params_b=4.0,
        source="NVIDIA Nemotron",
        trust_remote_code=True,
    ),
    # Fast tier — OpenBMB MiniCPM 4.1 8B. Ships custom modelling code (trust_remote_code).
    LocalModel(
        repo_id="openbmb/MiniCPM4.1-8B",
        profile="fast",
        params_b=8.0,
        source="OpenBMB MiniCPM",
        trust_remote_code=True,
    ),
    # Balanced tier — Cohere Labs Aya Expanse 8B (Command family, native transformers arch).
    # NOTE: this repo is *gated* — the Space's HF account must accept its licence and an
    # HF_TOKEN must be present for the weights to download.
    LocalModel(
        repo_id="CohereLabs/aya-expanse-8b",
        profile="balanced",
        params_b=8.0,
        source="Cohere Labs Aya",
    ),
    # Strong tier — JetBrains Mellum 2 (12B MoE, ~2.5B active). The Instruct variant (a
    # post-trained assistant with a chat template), not the Base completion model. Its card
    # loads it with AutoModelForMultimodalLM, so we pin that auto-class.
    LocalModel(
        repo_id="JetBrains/Mellum2-12B-A2.5B-Instruct",
        profile="strong",
        params_b=12.0,
        source="JetBrains Mellum",
        auto_class="AutoModelForMultimodalLM",
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
