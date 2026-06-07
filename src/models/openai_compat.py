from __future__ import annotations

import os
from dataclasses import dataclass, field

from src.models.provider import ModelProvider


@dataclass
class OpenAICompatProvider(ModelProvider):
    """Provider for any OpenAI-compatible chat completion API.

    Works with: OpenAI, Together AI, Groq, Ollama (v0.1.14+), HuggingFace TGI,
    NVIDIA NIM, and any endpoint that speaks the /v1/chat/completions protocol.

    Model selection and endpoint are driven by env vars so the scenario config
    never hard-codes a provider:
      OPENAI_API_KEY    — required for real calls
      OPENAI_BASE_URL   — optional, defaults to api.openai.com
      MODEL_NAME        — optional, defaults to gpt-4o-mini
      TINY_TITAN_MODE   — set to "1" to use a <=4B model profile
    """

    model: str = field(default_factory=lambda: os.getenv("MODEL_NAME", "gpt-4o-mini"))
    base_url: str | None = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL"))
    max_tokens: int = 256
    temperature: float = 0.9
    _client: object = field(default=None, init=False, repr=False)
    _last_usage: dict = field(default_factory=dict, init=False, repr=False)

    def _get_client(self):
        if self._client is None:
            try:
                import openai
            except ImportError as exc:
                raise ImportError(
                    "openai package is required for OpenAICompatProvider. "
                    "Run: pip install openai"
                ) from exc
            kwargs: dict = {}
            if self.base_url:
                kwargs["base_url"] = self.base_url
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
    """True when a usable API key is configured for live inference.

    Single source of truth for the online/offline decision, shared by
    ``build_from_env`` and the ModelRouter so they never disagree.
    """
    api_key = os.getenv("OPENAI_API_KEY", "")
    return bool(api_key) and api_key not in ("sk-stub", "your-key-here")


def build_from_env() -> ModelProvider:
    """Return the best available single provider based on environment configuration.

    Kept for backward compatibility with Phase-1 agents that take one provider.
    Manifest-driven agents use the per-profile :class:`ModelRouter` instead.
    """
    from src.models.provider import DeterministicTinyModel

    if has_live_credentials():
        return OpenAICompatProvider()
    return DeterministicTinyModel()
