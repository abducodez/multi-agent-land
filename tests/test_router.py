from __future__ import annotations

from src.models.litellm_provider import LiteLLMProvider
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
            specs={
                "tiny": ProfileSpec(
                    model="openai/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16",
                    base_url="https://ws--nemotron-3-nano-4b.modal.run/v1",
                    api_key="EMPTY",
                    temperature=0.5,
                    max_tokens=128,
                )
            },
        )
        provider = router.for_profile("tiny")
        assert isinstance(provider, LiteLLMProvider)
        assert provider.model == "openai/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"
        assert provider.api_base == "https://ws--nemotron-3-nano-4b.modal.run/v1"
        assert provider.api_key == "EMPTY"
        assert provider.temperature == 0.5
        assert provider.max_tokens == 128

    def test_default_decoding_applied_when_no_spec(self, monkeypatch):
        monkeypatch.setenv("MODEL_BALANCED", "mixtral-8x7b")
        router = ModelRouter(offline=False)
        provider = router.for_profile("balanced")
        assert provider.model == "mixtral-8x7b"
        assert provider.max_tokens == 320  # _PROFILE_DECODING["balanced"]


class TestFromEnv:
    def test_offline_without_binding(self, monkeypatch):
        # No Modal binding (and no stray cloud key) → deterministic offline stub.
        for var in ("MODAL_WORKSPACE", "MODAL_LLM_BASE_URL", "OPENAI_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        router = ModelRouter.from_env()
        assert router.offline is True
        assert isinstance(router.for_profile("fast"), DeterministicTinyModel)

    def test_online_with_modal_workspace(self, monkeypatch):
        # A Modal workspace is the activating signal for the live path.
        monkeypatch.setenv("MODAL_WORKSPACE", "my-workspace")
        router = ModelRouter.from_env()
        assert router.offline is False

    def test_online_with_modal_base_url(self, monkeypatch):
        # A single explicit OpenAI-compatible endpoint also activates live.
        monkeypatch.delenv("MODAL_WORKSPACE", raising=False)
        monkeypatch.setenv("MODAL_LLM_BASE_URL", "https://box.modal.run/v1")
        router = ModelRouter.from_env()
        assert router.offline is False
