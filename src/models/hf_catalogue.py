"""Hugging Face serverless inference catalogue — the second backend, next to Modal.

Where ``modal/catalogue.py`` describes models the project *deploys itself* (vLLM on
Modal GPUs), this module describes small instruct models reachable on the **Hugging
Face Inference Providers** router — a serverless, OpenAI-compatible gateway. There is
no serving side to operate: a single ``HF_TOKEN`` makes every model here callable, so
hooking up "many small models" is just appending one :class:`HFModel` below.

Like the Modal catalogue this file is **stdlib-only** and reaches no network: it is
pure data plus URL/string building, so the engine and the UI can read it offline (the
picker is populated even with no token) and a binding is derived only when a token is
present. The engine never imports a vendor SDK from here — calls route through the same
LiteLLM gateway as the Modal path, using the OpenAI-compatible custom-endpoint form
``openai/<repo_id>`` + ``api_base`` (the HF router's ``/v1`` URL).

Tier mapping mirrors the four logical profiles the cast routes by:
``tiny`` ≤4B · ``fast`` ≤8B · ``balanced`` ≤13B · ``strong`` ≤32B. Every model stays
within the project's ≤32B "small minds" rule; ``tiny`` honours the Tiny-Titan ≤4B band.

Add a model = append one :class:`HFModel`. Point a tier's default elsewhere = move the
``profile`` tag. Nothing downstream needs editing — the unified backend registry
(:mod:`src.models.inference`), the router, and the Lab picker all derive from this data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# The OpenAI-compatible router that fronts every Inference Provider. Overridable so a
# self-hosted TGI / a dedicated HF Inference Endpoint can stand in (it speaks the same
# REST API); the token then becomes that endpoint's bearer.
DEFAULT_BASE_URL = "https://router.huggingface.co/v1"

# Token env vars, in priority order. ``HF_TOKEN`` is the modern name; the older
# ``HUGGINGFACEHUB_API_TOKEN`` is accepted too so existing HF setups work unchanged.
_TOKEN_ENV_KEYS = ("HF_TOKEN", "HUGGINGFACEHUB_API_TOKEN")


@dataclass(frozen=True)
class HFModel:
    """One small model reachable on the HF Inference Providers router.

    ``repo_id`` is the Hugging Face repo (also the model id the router expects).
    ``profile`` is the tier this model is the default casting for (or None for an
    alternate the cast can still pin explicitly). ``source`` is a friendly family/org
    label for the picker. ``hf_provider`` optionally pins a specific Inference Provider
    (e.g. ``"together"``); None lets the router auto-select one.
    """

    repo_id: str
    profile: str | None = None
    params_b: float | None = None
    source: str = "Hugging Face"
    hf_provider: str | None = None

    @property
    def key(self) -> str:
        """Catalogue key (the repo id; the backend registry namespaces it as ``hf:<key>``)."""
        return self.repo_id

    @property
    def served_model_id(self) -> str:
        return self.repo_id


# --- The catalogue: small instruct models, grouped by the tier they default to -------
# A deliberately broad spread so a cast can mix families. Availability on the serverless
# router shifts over time; because this is plain data, retuning is a one-line edit.

HF_MODELS: tuple[HFModel, ...] = (
    # Only chat-capable model currently live on the enabled HF providers (free
    # `hf-inference`), verified by a real /v1/chat/completions call. Pinned to its
    # provider so the router does not depend on paid-provider auto-routing. It is
    # tagged `tiny` (1.5B, ≤4B band) but serves every tier: a tier with no dedicated
    # HF model falls back to the first catalogue entry (see lab._default_model_key),
    # so the whole cast routes here while only `hf-inference` is enabled.
    HFModel("katanemo/Arch-Router-1.5B", profile="tiny", params_b=1.5, source="Katanemo", hf_provider="hf-inference"),
    # NOTE: to use larger small models (e.g. openai/gpt-oss-20b — 20B, ≤32B, OpenAI
    # track) enable a provider that serves them (together / nscale / fireworks /
    # novita / groq) at https://huggingface.co/settings/inference-providers, then add
    # the model here. `HuggingFaceBio/Carbon-3B` is intentionally NOT listed: the HF
    # router rejects it as "not a chat model" (it is text-generation only), so it
    # cannot drive the chat-completions path the engine uses.
)


# --- engine-facing read view (mirrors modal_catalogue's dict shape) ------------------


def _build_entry(m: HFModel) -> dict:
    """One model as a plain dict, shaped like ``modal_catalogue.entries()``."""
    return {
        "key": m.key,
        "provider": m.source,
        "app": "hf-inference",
        "endpoint_name": m.repo_id,
        "served_model_id": m.served_model_id,
        "profile": m.profile,
        "params_b": m.params_b,
    }


# Built once at import (the catalogue is static): callers that mutate copy first.
_ENTRIES: tuple[dict, ...] = tuple(_build_entry(m) for m in HF_MODELS)
_ENTRY_BY_KEY: dict[str, dict] = {e["key"]: e for e in _ENTRIES}


def entries() -> list[dict]:
    """Every HF model as a plain dict, shaped like ``modal_catalogue.entries()``:

    ``{key, provider, app, endpoint_name, served_model_id, profile, params_b}`` —
    so the unified registry and the Lab picker treat both backends identically.
    ``provider`` is the friendly source label; ``app`` is the HF router id.
    """
    return list(_ENTRIES)


def entry_by_key(key: str) -> dict | None:
    """The catalogue entry whose key (the repo id) is *key*, or None."""
    return _ENTRY_BY_KEY.get(key)


def default_key_for_profile(profile: str) -> str | None:
    """The key of the model tagged for *profile* (first match), or None."""
    return next((m.key for m in HF_MODELS if m.profile == profile), None)


def _token(source: dict[str, str]) -> str:
    for var in _TOKEN_ENV_KEYS:
        val = source.get(var, "").strip()
        if val:
            return val
    return ""


def has_credentials(env: dict[str, str] | None = None) -> bool:
    """True when the HF backend is callable — a token, or an explicit base URL.

    An ``HF_INFERENCE_BASE_URL`` (a self-hosted TGI / dedicated endpoint) is enough on
    its own; otherwise the serverless router needs a token.
    """
    source = os.environ if env is None else env
    return bool(_token(source) or source.get("HF_INFERENCE_BASE_URL", "").strip())


def binding_for(key: str, env: dict[str, str] | None = None) -> dict:
    """Resolve a catalogue *key* into a concrete profile binding.

    Returns ``{"model", "base_url", "api_key"}`` where ``model`` is the LiteLLM
    OpenAI-compatible string ``openai/<repo_id>`` (with ``:provider`` appended when a
    model pins one), ``base_url`` is ``HF_INFERENCE_BASE_URL`` or the public router,
    and ``api_key`` is the HF token (``""`` when unset → the offline stub if nothing
    else is configured). Raises ``KeyError`` for an unknown key.
    """
    source = os.environ if env is None else env
    entry_model = next((m for m in HF_MODELS if m.key == key), None)
    if entry_model is None:
        known = sorted(m.key for m in HF_MODELS)
        raise KeyError(f"unknown HF model {key!r}; known: {known}")
    model_id = entry_model.repo_id
    if entry_model.hf_provider:
        model_id = f"{model_id}:{entry_model.hf_provider}"
    base_url = source.get("HF_INFERENCE_BASE_URL", "").strip() or DEFAULT_BASE_URL
    return {
        "model": f"openai/{model_id}",
        "base_url": base_url,
        "api_key": _token(source),
    }
