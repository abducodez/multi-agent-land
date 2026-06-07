from __future__ import annotations

import os
from dataclasses import dataclass, field

from src.models.provider import ModelProvider


def _default_compat_model() -> str:
    """The served model id this thin client sends by default.

    Pulls the ``balanced`` tier's model from the catalogue (the single source of
    truth) so there is no hard-coded model name to drift; falls back to a constant
    if the catalogue cannot be read. Note this is the *served id* (what a raw
    OpenAI-compatible call expects), not the ``openai/<id>`` LiteLLM string.
    """
    try:
        from src.models import modal_catalogue

        key = modal_catalogue.default_key_for_profile("balanced")
        if key:
            entry = modal_catalogue.entry_by_key(key)
            if entry:
                return entry["served_model_id"]
    except Exception:  # pragma: no cover - defensive: catalogue unavailable
        pass
    return "google/gemma-4-12B"


@dataclass
class OpenAICompatProvider(ModelProvider):
    """Thin client for any OpenAI-compatible chat-completions endpoint.

    The engine's live path routes through the LiteLLM gateway
    (:class:`~src.models.litellm_provider.LiteLLMProvider`); this client remains
    for the legacy single-provider :func:`build_from_env` path and as the home of
    the role→system personas. It targets the small models served on Modal — there
    is no OpenAI / generic-cloud default. Driven by env so no scenario hard-codes a
    provider:

      MODAL_LLM_BASE_URL — endpoint URL ending in /v1 (offline when unset)
      MODAL_LLM_KEY      — endpoint bearer token (a self-served vLLM accepts any;
                           defaults to "EMPTY")
      MODEL_BALANCED     — model id to send (falls back to the catalogue default)
    """

    model: str = field(default_factory=lambda: os.getenv("MODEL_BALANCED") or _default_compat_model())
    base_url: str | None = field(default_factory=lambda: os.getenv("MODAL_LLM_BASE_URL") or None)
    api_key: str | None = field(default_factory=lambda: os.getenv("MODAL_LLM_KEY") or None)
    max_tokens: int = 256
    temperature: float = 0.9
    _client: object = field(default=None, init=False, repr=False)
    _last_usage: dict = field(default_factory=dict, init=False, repr=False)

    def _get_client(self):
        if self._client is None:
            try:
                import openai
            except ImportError as exc:
                raise ImportError("openai package is required for OpenAICompatProvider. Run: uv add openai") from exc
            kwargs: dict = {}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            # A self-served vLLM endpoint accepts any token; the SDK still requires
            # one, so default to the conventional placeholder rather than erroring.
            kwargs["api_key"] = self.api_key or "EMPTY"
            self._client = openai.OpenAI(**kwargs)
        return self._client

    def complete(self, role: str, prompt: str) -> str:
        from src.models.provider import estimate_tokens

        client = self._get_client()
        system = self._system_for_role(role)
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            text = resp.choices[0].message.content.strip()
            usage = getattr(resp, "usage", None)
            if usage is not None:
                self._last_usage = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                    "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                    "total_tokens": getattr(usage, "total_tokens", 0) or 0,
                }
            else:
                p, c = estimate_tokens(prompt), estimate_tokens(text)
                self._last_usage = {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}
            return text
        except Exception as exc:
            self._last_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            return f"[model error: {exc}]"

    @staticmethod
    def _system_for_role(role: str) -> str:
        personas = {
            "seedkeeper": (
                "You are the Seedkeeper of Thousand Token Wood — an ancient, gentle observer "
                "who notices what grows, what fades, and what strange new thing just sprouted. "
                "Describe the world in one vivid, specific sentence. Be concrete and surprising. "
                "Do not explain. Do not moralize. Just observe."
            ),
            "mischief-critic": (
                "You are the Mischief Critic — a sharp-eyed judge who decides whether a scene "
                "is genuinely strange and playable or merely odd. "
                "Give a one-sentence verdict that names what works and what would make it stranger. "
                "Be encouraging but exacting."
            ),
            "pocket-actor": (
                "You are a Pocket Actor — a tiny character living inside the scene who wants "
                "something impossible and speaks with great urgency about it. "
                "Speak in first person. One or two sentences. Be specific and a little absurd."
            ),
            "echo": (
                "You are the Echo — you take whatever a visitor drops into the wood and return "
                "it transformed by the forest's logic. One sentence. Make it weirder and more alive."
            ),
            "clue-gatherer": (
                "You are a Clue Gatherer in a mystery scenario. "
                "Extract one specific, concrete clue from the current scene. "
                "State it plainly. Do not speculate."
            ),
            "hypothesis-former": (
                "You are a Hypothesis Former. Based on the clues so far, propose one testable "
                "explanation in a single sentence. Be specific. Start with 'Hypothesis:'."
            ),
            "devils-advocate": (
                "You are the Devil's Advocate. Challenge the current hypothesis with one "
                "specific counter-argument or overlooked fact. Be brief and sharp."
            ),
            "scene-whisperer": (
                "You are a scene whisperer for a magical forest world. "
                "Describe a new atmospheric detail in one vivid sentence. Be evocative."
            ),
        }
        return personas.get(role, f"You are a {role}. Respond in one or two sentences.")


def has_live_credentials() -> bool:
    """True when a live model binding is configured (else the offline stub runs).

    Single source of truth for the online/offline decision, shared by
    :func:`build_from_env` and the :class:`~src.models.router.ModelRouter` so they
    never disagree. The live path is the small models served on Modal (ADR-0015):
    either ``MODAL_WORKSPACE`` (the engine templates each profile's endpoint URL
    from it — see ``config/models.yaml`` + ``modal/catalogue.py``) or
    ``MODAL_LLM_BASE_URL`` (a single explicit OpenAI-compatible endpoint) is
    sufficient. There is no generic cloud key — everything routes to models you
    deploy yourself.
    """
    return bool(os.getenv("MODAL_WORKSPACE", "").strip() or os.getenv("MODAL_LLM_BASE_URL", "").strip())


def build_from_env() -> ModelProvider:
    """Return the best available single provider based on environment configuration.

    Kept for backward compatibility with Phase-1 agents that take one provider.
    Manifest-driven agents use the per-profile :class:`ModelRouter` instead.
    """
    from src.models.provider import DeterministicTinyModel

    if has_live_credentials():
        return OpenAICompatProvider()
    return DeterministicTinyModel()
