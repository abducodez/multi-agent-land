"""LiteLLM-backed provider — one gateway, every logical profile.

This is the *transport* the :class:`~src.models.router.ModelRouter` uses on the
live path: it replaces hand-rolled per-vendor SDK calls with a single idiomatic
``litellm.completion(...)`` call.  Routing (profile → concrete model + endpoint)
is unchanged and still lives in the router; this class only knows how to *call* a
model and report what it cost.

Two things it adds over the plain OpenAI-compatible provider:

  * **Real cost.**  LiteLLM prices the call from its model database, so the
    Governor's ``hourly_budget_usd`` becomes real on the live path.  Cost is
    exposed on ``last_usage["cost_usd"]`` (and ``last_cost``); offline it is 0.
  * **One model string for any endpoint.**  An OpenAI-compatible custom endpoint
    (the Modal/vLLM servers in ``modal/``) is reached with the LiteLLM model
    string ``openai/<served_model_id>`` plus an ``api_base`` — no per-vendor
    branching.

``litellm`` is imported lazily so ``import src.models.*`` (and ``import app``)
work with the package not installed; offline never touches this class.  The call
is kept thin and standard so a later layer can wrap it (e.g.
``instructor.from_litellm(litellm.completion)``) without fighting this code.
See ADR-0015.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.models.openai_compat import OpenAICompatProvider
from src.models.provider import ModelProvider


@dataclass
class LiteLLMProvider(ModelProvider):
    """Route one logical profile through the LiteLLM gateway.

    ``model`` is a LiteLLM model string.  For an OpenAI-compatible custom
    endpoint (Modal/vLLM) it is ``openai/<served_model_id>`` and ``api_base``
    points at the endpoint's ``/v1`` URL.  Decoding (``temperature`` /
    ``max_tokens``) and the binding come from the router's per-profile spec.
    """

    model: str
    api_base: str | None = None
    api_key: str | None = None
    temperature: float = 0.8
    max_tokens: int = 256
    _last_usage: dict = field(default_factory=dict, init=False, repr=False)
    _last_cost: float = field(default=0.0, init=False, repr=False)

    def complete(self, role: str, prompt: str) -> str:
        from src.models.provider import estimate_tokens

        try:
            import litellm
        except ImportError as exc:  # pragma: no cover - exercised only when unset
            raise ImportError(
                "litellm package is required for LiteLLMProvider. "
                "Install it with: uv pip install litellm"
            ) from exc

        system = OpenAICompatProvider._system_for_role(role)
        # A self-served vLLM endpoint accepts any token; default to the conventional
        # placeholder so a configured custom endpoint never trips on a missing key.
        api_key = self.api_key or ("EMPTY" if self.api_base else None)
        try:
            response = litellm.completion(
                model=self.model,
                api_base=self.api_base,
                api_key=api_key,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            text = (response.choices[0].message.content or "").strip()
            usage = getattr(response, "usage", None)
            if usage is not None:
                prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
                completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
                total_tokens = int(getattr(usage, "total_tokens", 0) or 0) or (
                    prompt_tokens + completion_tokens
                )
            else:
                prompt_tokens, completion_tokens = estimate_tokens(prompt), estimate_tokens(text)
                total_tokens = prompt_tokens + completion_tokens
            cost = self._extract_cost(litellm, response)
            self._last_cost = cost
            self._last_usage = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cost_usd": cost,
            }
            return text
        except Exception as exc:
            self._last_cost = 0.0
            self._last_usage = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
            }
            return f"[model error: {exc}]"

    @property
    def last_cost(self) -> float:
        """Metered USD cost of the most recent :meth:`complete` call (0.0 offline)."""
        return self._last_cost

    @staticmethod
    def _extract_cost(litellm, response) -> float:
        """Best-effort USD cost for *response*; 0.0 if the model is unpriced.

        Prefers the value LiteLLM already attached during the call
        (``_hidden_params["response_cost"]``); falls back to pricing the response
        directly.  Both paths are guarded — an unknown/custom model (e.g. a
        self-served vLLM endpoint) simply yields 0.0 rather than raising.
        """
        hidden = getattr(response, "_hidden_params", None)
        if isinstance(hidden, dict):
            cost = hidden.get("response_cost")
            if isinstance(cost, (int, float)):
                return float(cost)
        try:
            cost = litellm.completion_cost(completion_response=response)
            return float(cost or 0.0)
        except Exception:
            return 0.0
