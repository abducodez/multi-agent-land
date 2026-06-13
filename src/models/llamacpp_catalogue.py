"""llama.cpp local-inference catalogue — the third backend, next to Modal and HF.

Where ``modal/catalogue.py`` describes models the project deploys itself (vLLM on
Modal GPUs) and ``hf_catalogue.py`` describes models reachable on Hugging Face's
serverless router, this module describes **GGUF models you run on your own machine**
through ``llama-server`` — llama.cpp's OpenAI-compatible HTTP server. It is the
"Llama Champion" lane: a real llama.cpp runtime in the cast, and the same data also
carries the **NVIDIA Nemotron** and **OpenBMB MiniCPM** small models, so one local
server can qualify several sponsor tracks at once.

Like the other catalogues this file is **stdlib-only** and reaches no network: it is
pure data plus string building, so the engine and the Lab picker can read it offline.
The engine never imports a vendor SDK from here — calls route through the same LiteLLM
gateway as the Modal/HF paths, using the OpenAI-compatible custom-endpoint form
``openai/<served_id>`` + ``api_base`` (the local server's ``/v1`` URL). The *serving*
side — picking a GGUF, detecting a GPU, and launching ``llama-server`` — lives in the
sibling :mod:`src.models.llamacpp_server`; this module only describes the models and
how to reach a running server.

Tier mapping mirrors the four logical profiles the cast routes by:
``tiny`` ≤4B · ``fast`` ≤8B · ``balanced`` ≤13B · ``strong`` ≤32B. Every model stays
within the project's ≤32B "small minds" rule; ``tiny`` honours the Tiny-Titan ≤4B band.

Add a model = append one :class:`LlamaCppModel`. Nothing downstream needs editing —
the unified backend registry (:mod:`src.models.inference`), the router, the Lab picker,
and the launcher all derive from this data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# The local llama.cpp server's OpenAI-compatible base URL. llama-server's default bind
# is 127.0.0.1:8080; the launcher prints the matching ``LLAMACPP_BASE_URL`` export so the
# engine and the server agree. Overridable for a remote box or a non-default port.
DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"

# Base-URL env var (the opt-in seam: set this and the backend goes live). A llama.cpp
# server needs no real token, but LiteLLM/OpenAI clients require *some* bearer string —
# ``LLAMACPP_API_KEY`` overrides the harmless default below.
_BASE_URL_ENV = "LLAMACPP_BASE_URL"
_API_KEY_ENV = "LLAMACPP_API_KEY"
_DEFAULT_API_KEY = "llama.cpp"  # any non-empty string; llama-server ignores its value


@dataclass(frozen=True)
class LlamaCppModel:
    """One GGUF model servable locally through ``llama-server``.

    Engine-facing fields (``key`` / ``profile`` / ``params_b`` / ``served_id``) mirror
    the other catalogues so the registry treats all three backends identically. The
    serving fields (``hf_repo`` / ``quant`` / ``gguf_file`` / ``ctx_size`` / sampling /
    ``flash_attn`` / ``reasoning``) are read only by :mod:`src.models.llamacpp_server`
    when it assembles the ``llama-server`` command — the engine never touches them.
    """

    # Identity / engine-facing
    key: str  # stable slug + served-model id, e.g. "nemotron-3-nano-4b"
    hf_repo: str  # Hugging Face GGUF repo, e.g. "nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF"
    profile: str | None = None  # default tier (tiny/fast/balanced/strong) or None
    params_b: float | None = None  # total params in billions (docs / Tiny-Titan checks)
    source: str = "llama.cpp"  # friendly family/org label for the picker

    # Serving / launcher-facing
    quant: str | None = "Q4_K_M"  # GGUF quant tag for the ``-hf repo:QUANT`` form; None
    #                               when the quant is already baked into ``hf_repo``.
    gguf_file: str | None = None  # explicit GGUF filename (for ``-m``/download); optional
    ctx_size: int = 8192  # default --ctx-size; 0 means "use the model's trained context"
    temperature: float = 0.7
    top_p: float = 0.95
    top_k: int = 40
    flash_attn: bool = True  # --flash-attn (-fa): faster + lower memory where supported
    reasoning: bool = False  # a "thinking" model — budget more tokens; nudge sampling

    @property
    def served_model_id(self) -> str:
        """Model id clients pass. We launch with ``--alias <key>`` so the running server
        reports this exact id, keeping the binding's ``openai/<id>`` stable across the
        repo/quant churn of GGUF names."""
        return self.key

    @property
    def hf_spec(self) -> str:
        """The argument for ``llama-server -hf`` — ``repo`` or ``repo:QUANT``.

        Some repos bake the quant into the repo name (e.g. JetBrains' ``…-Q4_K_M``); for
        those ``quant`` is None and the bare repo is used. Others publish many quants in
        one repo and need the ``:QUANT`` selector.
        """
        return f"{self.hf_repo}:{self.quant}" if self.quant else self.hf_repo


# --- The catalogue: small GGUF models, one per default tier ---------------------------
# A deliberate spread across sponsor families so a single local server fills the cast and
# stacks prize lanes (Llama Champion + Nemotron + OpenBMB). Plain data — retuning a tier
# or adding a quant is a one-line edit.

LLAMACPP_MODELS: tuple[LlamaCppModel, ...] = (
    # NVIDIA Nemotron 3 Nano 4B — tiny tier, ≤4B Tiny-Titan band. Repo publishes several
    # quants, so the quant is selected via the ``:Q4_K_M`` form.
    LlamaCppModel(
        key="nemotron-3-nano-4b",
        hf_repo="nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF",
        profile="tiny",
        params_b=4.0,
        source="NVIDIA Nemotron",
        quant="Q4_K_M",
        temperature=0.7,
    ),
    # OpenBMB MiniCPM 4.1 8B — fast tier (≤8B). Multi-quant repo → ``:Q4_K_M``.
    LlamaCppModel(
        key="minicpm-4-1-8b",
        hf_repo="openbmb/MiniCPM4.1-8B-GGUF",
        profile="fast",
        params_b=8.0,
        source="OpenBMB MiniCPM",
        quant="Q4_K_M",
        gguf_file="MiniCPM4.1-8B-Q4_K_M.gguf",
        temperature=0.8,
    ),
    # JetBrains Mellum 2 12B-A2.5B — balanced tier (≤13B; MoE, ~2.5B active). A "thinking"
    # model: budget more tokens and use its recommended sampling. The quant is baked into
    # the repo name, so ``quant`` is None (the bare ``-hf`` repo is used).
    LlamaCppModel(
        key="mellum2-12b-thinking",
        hf_repo="JetBrains/Mellum2-12B-A2.5B-Thinking-GGUF-Q4_K_M",
        profile="balanced",
        params_b=12.0,
        source="JetBrains Mellum",
        quant=None,
        gguf_file="Mellum2-12B-A2.5B-Thinking-Q4_K_M.gguf",
        ctx_size=16384,
        temperature=0.6,
        top_p=0.95,
        top_k=20,
        reasoning=True,
    ),
)


# --- engine-facing read view (mirrors modal_catalogue / hf_catalogue dict shape) ------


def _build_entry(m: LlamaCppModel) -> dict:
    """One model as a plain dict, shaped like ``modal_catalogue.entries()``."""
    return {
        "key": m.key,
        "provider": m.source,
        "app": "llama.cpp",
        "endpoint_name": m.key,
        "served_model_id": m.served_model_id,
        "profile": m.profile,
        "params_b": m.params_b,
    }


# Built once at import (the catalogue is static): callers that mutate copy first.
_ENTRIES: tuple[dict, ...] = tuple(_build_entry(m) for m in LLAMACPP_MODELS)
_ENTRY_BY_KEY: dict[str, dict] = {e["key"]: e for e in _ENTRIES}
_MODEL_BY_KEY: dict[str, LlamaCppModel] = {m.key: m for m in LLAMACPP_MODELS}


def entries() -> list[dict]:
    """Every local model as a plain dict, shaped like the other catalogues:

    ``{key, provider, app, endpoint_name, served_model_id, profile, params_b}`` — so the
    unified registry and the Lab picker treat all three backends identically.
    """
    return list(_ENTRIES)


def entry_by_key(key: str) -> dict | None:
    """The catalogue entry whose key is *key*, or None."""
    return _ENTRY_BY_KEY.get(key)


def model_by_key(key: str) -> LlamaCppModel | None:
    """The full :class:`LlamaCppModel` for *key* (serving fields included), or None.

    The launcher uses this; the engine path only needs :func:`binding_for`.
    """
    return _MODEL_BY_KEY.get(key)


def default_key_for_profile(profile: str) -> str | None:
    """The key of the model tagged for *profile* (first match), or None."""
    return next((m.key for m in LLAMACPP_MODELS if m.profile == profile), None)


def base_url(env: dict[str, str] | None = None) -> str:
    """The configured local server base URL, or the llama-server default."""
    source = os.environ if env is None else env
    return source.get(_BASE_URL_ENV, "").strip() or DEFAULT_BASE_URL


def has_credentials(env: dict[str, str] | None = None) -> bool:
    """True when the llama.cpp backend is opted in — an explicit ``LLAMACPP_BASE_URL``.

    Unlike the hosted backends there is no token to gate on: a local ``llama-server``
    needs no auth. We gate on the base URL being *set* so the backend never silently
    claims to be live when nothing is running — the launcher sets this for you, or you
    export it by hand to point at an already-running (or remote) server.
    """
    source = os.environ if env is None else env
    return bool(source.get(_BASE_URL_ENV, "").strip())


def binding_for(key: str, env: dict[str, str] | None = None) -> dict:
    """Resolve a catalogue *key* into a concrete profile binding.

    Returns ``{"model", "base_url", "api_key"}`` where ``model`` is the LiteLLM
    OpenAI-compatible string ``openai/<served_id>``, ``base_url`` is ``LLAMACPP_BASE_URL``
    (or the llama-server default), and ``api_key`` is ``LLAMACPP_API_KEY`` (or a harmless
    placeholder — llama-server ignores it). Raises ``KeyError`` for an unknown key.
    """
    source = os.environ if env is None else env
    model = _MODEL_BY_KEY.get(key)
    if model is None:
        known = sorted(_MODEL_BY_KEY)
        raise KeyError(f"unknown llama.cpp model {key!r}; known: {known}")
    api_key = source.get(_API_KEY_ENV, "").strip() or _DEFAULT_API_KEY
    return {
        "model": f"openai/{model.served_model_id}",
        "base_url": base_url(source),
        "api_key": api_key,
    }
