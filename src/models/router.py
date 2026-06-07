"""Model router — per-agent small-model selection by logical profile.

Each agent declares a *profile* in its manifest (``tiny`` | ``fast`` |
``balanced`` | ``strong``).  The router resolves that profile to a concrete
model name, endpoint, and decoding config, and hands back a ready provider.
This is the single place per-agent model selection happens, so:

  - swapping a profile to a different small model is a one-line config change;
  - a scenario can mix a ``tiny`` worker with a ``strong`` judge for free;
  - the rest of the engine never names a model.

Offline (no API key) the router serves a :class:`DeterministicTinyModel` for
every profile, so demos and tests run with zero inference and full
reproducibility.  See ADR-0010.

On the live path the concrete transport is the :class:`LiteLLMProvider` gateway
(ADR-0015): profiles point at the OpenAI-compatible Modal/vLLM endpoints in
``modal/`` and the gateway reports real per-call cost into the Governor.  The
routing abstraction here is unchanged — only how a model is *called* moved.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.manifest import ModelProfile, resolve_model
from src.models.openai_compat import has_live_credentials
from src.models.provider import DeterministicTinyModel, ModelProvider

# Decoding defaults per profile.  Smaller models stay cooler and shorter; the
# strong judge/reflector tier gets more room.  Override per-profile via config.
_PROFILE_DECODING: dict[str, dict[str, float | int]] = {
    "tiny": {"temperature": 0.7, "max_tokens": 160},
    "fast": {"temperature": 0.9, "max_tokens": 220},
    "balanced": {"temperature": 0.8, "max_tokens": 320},
    "strong": {"temperature": 0.6, "max_tokens": 480},
}


@dataclass
class ProfileSpec:
    """Concrete binding for one logical profile."""

    model: str
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = 0.8
    max_tokens: int = 256


@dataclass
class ModelRouter:
    """Resolves a logical model profile to a concrete provider, with caching.

    Construct with explicit ``specs`` (e.g. from a validated config) or call
    :meth:`from_env` to derive them from ``MODEL_TINY`` / ``MODEL_FAST`` /
    ``MODEL_BALANCED`` / ``MODEL_STRONG`` (falling back to the catalogue default
    for each tier — see :func:`~src.core.manifest.resolve_model`).
    """

    specs: dict[str, ProfileSpec] = field(default_factory=dict)
    offline: bool = False
    _cache: dict[str, ModelProvider] = field(default_factory=dict, init=False, repr=False)

    # ── resolution ──────────────────────────────────────────────────────────

    def for_profile(self, profile: str) -> ModelProvider:
        """Return (and cache) the provider bound to *profile*."""
        if profile not in self._cache:
            self._cache[profile] = self._build(profile)
        return self._cache[profile]

    def complete(self, role: str, prompt: str, profile: ModelProfile = "fast") -> str:
        """Convenience: route by profile and complete in one call."""
        return self.for_profile(profile).complete(role, prompt)

    def describe(self) -> dict[str, str]:
        """Human-readable profile → model map for the UI/stats panel."""
        if self.offline:
            return {p: f"stub:{p} (deterministic)" for p in _PROFILE_DECODING}
        return {p: self._spec_for(p).model for p in _PROFILE_DECODING}

    # ── internals ───────────────────────────────────────────────────────────

    def _build(self, profile: str) -> ModelProvider:
        if self.offline:
            return DeterministicTinyModel(variant=f"stub:{profile}")
        # Live transport is the LiteLLM gateway (ADR-0015).  Lazy-import keeps the
        # offline path free of the dependency.
        from src.models.litellm_provider import LiteLLMProvider

        spec = self._spec_for(profile)
        return LiteLLMProvider(
            model=spec.model,
            api_base=spec.base_url,
            api_key=spec.api_key,
            temperature=spec.temperature,
            max_tokens=spec.max_tokens,
        )

    def _spec_for(self, profile: str) -> ProfileSpec:
        if profile in self.specs:
            return self.specs[profile]
        decoding = _PROFILE_DECODING.get(profile, _PROFILE_DECODING["fast"])
        return ProfileSpec(
            model=resolve_model(profile),  # type: ignore[arg-type]
            temperature=float(decoding["temperature"]),
            max_tokens=int(decoding["max_tokens"]),
        )

    # ── factory ─────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "ModelRouter":
        """Build a router from environment configuration.

        Offline (deterministic stub for every profile) unless a live API key is
        present, in which case each profile resolves to its concrete model via
        ``resolve_model`` plus the per-profile decoding defaults.
        """
        return cls(offline=not has_live_credentials())
