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
is kept thin and standard so the structured layer can wrap it
(``instructor.from_litellm(litellm.completion)``) without fighting this code.
See ADR-0015.

On top of the plain :meth:`complete`, this gateway also offers
:meth:`complete_structured`: it wraps the same ``litellm.completion`` with
Instructor to return a *validated* Pydantic instance (kind constrained to the
agent's grant, retried on validation failure), reading usage and cost from the
raw completion exactly as :meth:`complete` does.  ``instructor`` is likewise
lazy-imported, so the offline path needs neither it nor ``litellm``.  See
ADR-0016.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.models.openai_compat import OpenAICompatProvider
from src.models.provider import ModelProvider, model_error

if TYPE_CHECKING:
    from pydantic import BaseModel


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
    max_retries: int = 2
    """Validation retries for :meth:`complete_structured` (live structured output)."""
    num_retries: int = 2
    """Transport retries LiteLLM makes on a transient call failure — a dropped
    connection, a timeout, a 5xx.  Lets a flaky endpoint self-heal mid-demo before the
    call gives up and returns the failure sentinel."""
    _last_usage: dict = field(default_factory=dict, init=False, repr=False)
    _last_cost: float = field(default=0.0, init=False, repr=False)
    _last_reasoning: str = field(default="", init=False, repr=False)

    def complete(self, role: str, prompt: str) -> str:
        litellm = self._litellm()
        try:
            response = litellm.completion(
                model=self.model,
                api_base=self.api_base,
                api_key=self._resolved_api_key(),
                messages=self._messages(role, prompt),
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                num_retries=self.num_retries,
            )
            text = (response.choices[0].message.content or "").strip()
            self._capture_usage(litellm, response, prompt, text)
            return text
        except Exception as exc:
            self._zero_usage()
            return model_error(exc)

    def complete_structured(
        self,
        role: str,
        prompt: str,
        response_model: type["BaseModel"],
    ) -> "BaseModel":
        """Return a validated *response_model* instance via Instructor.

        Wraps the same ``litellm.completion`` with
        ``instructor.from_litellm`` and asks for *response_model*, retrying a few
        times on validation failure.  Because the model is constrained and
        re-prompted until it validates, the caller gets a typed, schema-valid
        object — the live path never falls back to wrapping malformed prose
        (see ADR-0016).  Usage and cost are read from the raw completion exactly
        as :meth:`complete` does, so token/cost metering is unchanged.

        ``instructor`` is imported lazily; offline never reaches this method.
        On error the usage is zeroed and the exception propagates so the caller
        can fall back to the prompt-and-parse path.
        """
        litellm = self._litellm()
        try:
            import instructor
        except ImportError as exc:  # pragma: no cover - exercised only when unset
            raise ImportError(
                "instructor package is required for complete_structured(). Install it with: uv pip install instructor"
            ) from exc

        client = instructor.from_litellm(litellm.completion)
        try:
            result, response = client.create_with_completion(
                model=self.model,
                api_base=self.api_base,
                api_key=self._resolved_api_key(),
                messages=self._messages(role, prompt),
                response_model=response_model,
                max_retries=self.max_retries,
                num_retries=self.num_retries,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            text = getattr(result, "text", "") or ""
            self._capture_usage(litellm, response, prompt, text)
            return result
        except Exception:
            self._zero_usage()
            raise

    @property
    def last_reasoning(self) -> str:
        """The model's separated thinking from the most recent call, or "".

        Reasoning models served on vLLM (e.g. the gemma4 / qwen3 reasoning parsers)
        return their chain-of-thought in ``message.reasoning_content``, leaving
        ``content`` for the answer. We surface it so the UI can show it under the
        mind-reader toggle — it is never fed back into any agent's prompt."""
        return self._last_reasoning

    @property
    def last_cost(self) -> float:
        """Metered USD cost of the most recent call (0.0 offline)."""
        return self._last_cost

    # ── call helpers (shared by complete / complete_structured) ─────────────────

    @staticmethod
    def _litellm():
        """Lazy-import litellm; raise a clear install hint if it is missing."""
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover - exercised only when unset
            raise ImportError(
                "litellm package is required for LiteLLMProvider. Install it with: uv pip install litellm"
            ) from exc
        return litellm

    @staticmethod
    def _messages(role: str, prompt: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": OpenAICompatProvider._system_for_role(role)},
            {"role": "user", "content": prompt},
        ]

    def _resolved_api_key(self) -> str | None:
        # A self-served vLLM endpoint accepts any token; default to the conventional
        # placeholder so a configured custom endpoint never trips on a missing key.
        return self.api_key or ("EMPTY" if self.api_base else None)

    def _capture_usage(self, litellm, response, prompt: str, text: str) -> None:
        """Record tokens + cost from *response* onto ``last_usage``/``last_cost``."""
        from src.models.provider import estimate_tokens

        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            total_tokens = int(getattr(usage, "total_tokens", 0) or 0) or (prompt_tokens + completion_tokens)
        else:
            prompt_tokens, completion_tokens = estimate_tokens(prompt), estimate_tokens(text)
            total_tokens = prompt_tokens + completion_tokens
        cost = self._extract_cost(litellm, response)
        self._last_cost = cost
        self._last_reasoning = self._extract_reasoning(response)
        self._last_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost,
        }

    def _zero_usage(self) -> None:
        self._last_cost = 0.0
        self._last_reasoning = ""
        self._last_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
        }

    @staticmethod
    def _extract_reasoning(response) -> str:
        """Pull the model's separated thinking from *response*, or "".

        vLLM reasoning parsers surface it as ``message.reasoning_content`` (some
        providers as ``reasoning`` or under ``provider_specific_fields``). All
        access is defensive — a non-reasoning model simply yields ""."""
        try:
            message = response.choices[0].message
        except (AttributeError, IndexError, TypeError):
            return ""
        candidates = [getattr(message, "reasoning_content", None), getattr(message, "reasoning", None)]
        psf = getattr(message, "provider_specific_fields", None)
        if isinstance(psf, dict):
            candidates += [psf.get("reasoning_content"), psf.get("reasoning")]
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

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
