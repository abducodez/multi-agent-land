from __future__ import annotations

from src.models.openai_compat import OpenAICompatProvider
from src.models.provider import DeterministicTinyModel, ModelProvider, estimate_tokens
from src.models.router import ModelRouter, ProfileSpec


class TestEstimateTokens:
    def test_non_zero(self):
        assert estimate_tokens("") >= 1
        assert estimate_tokens("a" * 40) >= 10


class TestModelRouterOffline:
    def test_for_profile_returns_provider(self):
        router = ModelRouter(offline=True)
        for profile in ("tiny", "fast", "balanced", "strong"):
            assert isinstance(router.for_profile(profile), ModelProvider)

    def test_offline_serves_deterministic_stub(self):
        router = ModelRouter(offline=True)
        assert isinstance(router.for_profile("tiny"), DeterministicTinyModel)

    def test_distinct_variant_per_profile(self):
        router = ModelRouter(offline=True)
        assert router.for_profile("tiny").variant != router.for_profile("strong").variant

    def test_caches_provider_instance(self):
        router = ModelRouter(offline=True)
        assert router.for_profile("fast") is router.for_profile("fast")

    def test_complete_routes_and_records_usage(self):
        router = ModelRouter(offline=True)
        out = router.complete("scene-whisperer", "grow the wood", profile="tiny")
        assert isinstance(out, str) and out
        usage = router.for_profile("tiny").last_usage
        assert usage["total_tokens"] > 0

    def test_describe_lists_all_profiles(self):
        desc = ModelRouter(offline=True).describe()
        assert set(desc) == {"tiny", "fast", "balanced", "strong"}


class TestModelRouterOnline:
    def test_explicit_spec_used(self):
        router = ModelRouter(
            offline=False,
            specs={"tiny": ProfileSpec(model="qwen2.5-3b-instruct", temperature=0.5, max_tokens=128)},
        )
        provider = router.for_profile("tiny")
        assert isinstance(provider, OpenAICompatProvider)
        assert provider.model == "qwen2.5-3b-instruct"
        assert provider.temperature == 0.5
        assert provider.max_tokens == 128

    def test_default_decoding_applied_when_no_spec(self, monkeypatch):
        monkeypatch.setenv("MODEL_BALANCED", "mixtral-8x7b")
        router = ModelRouter(offline=False)
        provider = router.for_profile("balanced")
        assert provider.model == "mixtral-8x7b"
        assert provider.max_tokens == 320  # _PROFILE_DECODING["balanced"]


class TestFromEnv:
    def test_offline_without_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        router = ModelRouter.from_env()
        assert router.offline is True
        assert isinstance(router.for_profile("fast"), DeterministicTinyModel)

    def test_online_with_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-real-looking-key")
        router = ModelRouter.from_env()
        assert router.offline is False
