"""Inference backend registry — one façade over every place a model can run.

The engine started with a single inference backend: the OpenAI-compatible vLLM
endpoints the project serves on Modal (``modal/catalogue.py``). This module adds a
*second* backend — Hugging Face's serverless Inference Providers
(``hf_catalogue.py``) — and gives both a single, uniform read surface so the router,
the config loader, and the Lab UI never special-case which backend a model lives on.

A model is named by a **backend-qualified key**: ``"<backend>:<raw_key>"``, e.g.
``"hf:Qwen/Qwen2.5-7B-Instruct"``. A *bare* key with no recognised prefix means the
Modal backend — so every existing key, manifest ``model_endpoint``, and
``config/models.yaml`` ``endpoint:`` keeps working untouched (Modal is the default
backend). The router resolves a qualified key to the right backend's binding; offline
it folds into the deterministic stub like any profile, so demos stay reproducible.

Each backend exposes the same three primitives (``entries`` / ``binding_for`` /
``default_key_for_profile``); this layer dispatches on the prefix and adds a
``backend`` tag to every entry. Adding a third backend later = add one entry to
``_BACKENDS`` — nothing above this module changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.models import hf_catalogue, local_catalogue, modal_catalogue

# Separator between a backend prefix and the backend-local key. A raw HF repo id can
# contain ``/`` but never a leading ``<backend>:`` prefix, so a single split is safe.
SEP = ":"


@dataclass(frozen=True)
class Backend:
    """One inference backend and the description the UI shows for it."""

    key: str  # stable handle / key prefix, e.g. "modal"
    label: str  # short display name, e.g. "Modal"
    blurb: str  # one-line UX description
    catalogue: object  # the backing catalogue module (modal_catalogue / hf_catalogue)


# Modal first — it is the default backend a bare key resolves to.
_BACKENDS: dict[str, Backend] = {
    "modal": Backend(
        key="modal",
        label="Modal",
        blurb="self-hosted vLLM endpoints you deploy (full control, GPU-backed)",
        catalogue=modal_catalogue,
    ),
    "hf": Backend(
        key="hf",
        label="Hugging Face",
        blurb="serverless Inference Providers — many small models, just a token",
        catalogue=hf_catalogue,
    ),
    "local": Backend(
        key="local",
        label="Local GPU",
        blurb="transformers in-process on this Space's own GPU — ZeroGPU or dedicated",
        catalogue=local_catalogue,
    ),
}

DEFAULT_BACKEND = "modal"


# ── key (de)composition ──────────────────────────────────────────────────────────


def backends() -> list[Backend]:
    """Every registered backend, in display order (Modal first)."""
    return list(_BACKENDS.values())


def split_key(key: str) -> tuple[str, str]:
    """Split a (possibly qualified) key into ``(backend, raw_key)``.

    ``"hf:org/model"`` → ``("hf", "org/model")``; a bare key (no recognised prefix,
    e.g. ``"gemma-4-12b"``) → ``("modal", "gemma-4-12b")`` for backward compatibility.
    """
    if SEP in key:
        prefix, _, rest = key.partition(SEP)
        if prefix in _BACKENDS:
            return prefix, rest
    return DEFAULT_BACKEND, key


def qualify(backend: str, raw_key: str) -> str:
    """Build a backend-qualified key. Modal keys stay *bare* (the historical form, so
    existing config/tests are unaffected); every other backend is prefixed."""
    if backend == DEFAULT_BACKEND:
        return raw_key
    return f"{backend}{SEP}{raw_key}"


def _catalogue(backend: str):
    backend_obj = _BACKENDS.get(backend)
    return backend_obj.catalogue if backend_obj else None


# ── unified read surface ───────────────────────────────────────────────────────────


def entries(backend: str | None = None) -> list[dict]:
    """Catalogue entries, each tagged with its ``backend`` and a qualified ``key``.

    With *backend* set, only that backend's models; otherwise every backend's, in
    backend order. Each dict is the backend's own entry shape plus ``backend`` and
    with ``key`` rewritten to the qualified form the rest of the engine passes around.
    """
    wanted = [backend] if backend is not None else list(_BACKENDS)
    out: list[dict] = []
    for name in wanted:
        cat = _catalogue(name)
        if cat is None:
            continue
        for entry in cat.entries():
            tagged = dict(entry)
            tagged["backend"] = name
            tagged["key"] = qualify(name, entry["key"])
            out.append(tagged)
    return out


def entry_by_key(key: str) -> dict | None:
    """The entry for a (qualified or bare) *key*, tagged with its backend, or None."""
    backend, raw = split_key(key)
    cat = _catalogue(backend)
    if cat is None:
        return None
    try:
        entry = cat.entry_by_key(raw)
    except Exception:  # pragma: no cover - defensive: a broken catalogue → no entry
        return None
    if entry is None:
        return None
    tagged = dict(entry)
    tagged["backend"] = backend
    tagged["key"] = qualify(backend, entry["key"])
    return tagged


def binding_for(key: str, env: dict[str, str] | None = None) -> dict:
    """Resolve a (qualified or bare) *key* to ``{model, base_url, api_key}``.

    Dispatches to the owning backend's ``binding_for``. Raises ``KeyError`` for an
    unknown backend or key — a profile pointing at a non-existent model is a config
    error worth surfacing (mirrors ``modal_catalogue.binding_for``).
    """
    backend, raw = split_key(key)
    cat = _catalogue(backend)
    if cat is None:
        raise KeyError(f"unknown inference backend {backend!r} for key {key!r}; known: {sorted(_BACKENDS)}")
    return cat.binding_for(raw, env=env)


def default_key_for_profile(profile: str, backend: str = DEFAULT_BACKEND) -> str | None:
    """The qualified key of *backend*'s default model for *profile*, or None."""
    cat = _catalogue(backend)
    if cat is None:
        return None
    raw = cat.default_key_for_profile(profile)
    return qualify(backend, raw) if raw else None


# ── credentials / status (per backend) ─────────────────────────────────────────────


def backend_label(backend: str) -> str:
    """The display label for *backend* (its key as a fallback)."""
    b = _BACKENDS.get(backend)
    return b.label if b else backend


def backend_available(backend: str, env: dict[str, str] | None = None) -> bool:
    """True when *backend* has enough configuration to make a live call.

    Each backend's catalogue owns its own credential check (``has_credentials``), so
    this dispatches without naming any backend — adding a backend to ``_BACKENDS`` makes
    it work here for free.  Single source of truth for "can this backend go live", shared
    by the UI chip and the composed-run offline decision.
    """
    source = os.environ if env is None else env
    b = _BACKENDS.get(backend)
    return bool(b and b.catalogue.has_credentials(source))


def configured_backends(env: dict[str, str] | None = None) -> list[str]:
    """The keys of every backend that is currently live-capable (in display order)."""
    return [b.key for b in backends() if backend_available(b.key, env=env)]
