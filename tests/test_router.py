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
        assert provider.max_tokens == 768  # _PROFILE_DECODING["balanced"] (reasoning headroom)


class TestModelRouterCatalogueEndpoint:
    """A non-tier router key names a specific catalogue model (manifest.model_endpoint)."""

    def test_offline_endpoint_key_serves_distinct_stub(self):
        # A concrete catalogue key routes like any profile offline: the deterministic
        # stub, with the key folded into its variant so the choice still varies output.
        router = ModelRouter(offline=True)
        provider = router.for_profile("minicpm-4-1-8b")
        assert isinstance(provider, DeterministicTinyModel)
        assert "minicpm-4-1-8b" in provider.variant
        assert provider.variant != router.for_profile("gemma-4-12b").variant

    def test_online_endpoint_key_resolves_to_catalogue_binding(self, monkeypatch):
        monkeypatch.setenv("MODAL_WORKSPACE", "demo-ws")
        monkeypatch.setenv("MODAL_LLM_KEY", "EMPTY")
        monkeypatch.delenv("MODEL_BALANCED", raising=False)
        router = ModelRouter(offline=False)
        provider = router.for_profile("gemma-4-12b")
        assert isinstance(provider, LiteLLMProvider)
        assert provider.model == "openai/google/gemma-4-12B"
        assert "gemma-4-12b" in provider.api_base
        assert provider.max_tokens == 768  # balanced tier decoding (reasoning headroom)

    def test_unbound_specialist_uses_balanced_decoding(self, monkeypatch):
        # nemotron-cascade-14b has profile=None → balanced decoding defaults.
        monkeypatch.setenv("MODAL_WORKSPACE", "demo-ws")
        router = ModelRouter(offline=False)
        provider = router.for_profile("nemotron-cascade-14b")
        assert isinstance(provider, LiteLLMProvider)
        assert provider.max_tokens == 768  # balanced tier decoding

    def test_unknown_key_degrades_to_fast_tier(self, monkeypatch):
        monkeypatch.setenv("MODEL_FAST", "fallback-model")
        router = ModelRouter(offline=False)
        provider = router.for_profile("not-a-real-endpoint")
        assert provider.model == "fallback-model"
        assert provider.max_tokens == 320  # fast tier decoding

    def test_online_hf_endpoint_key_resolves_to_hf_router(self, monkeypatch):
        # A backend-qualified HF key resolves to the HF Inference router binding —
        # the OpenAI-compatible model string + HF token, no Modal env needed. The
        # model pins its provider (hf-inference) so routing needs no paid auto-select.
        monkeypatch.setenv("HF_TOKEN", "hf_secret")
        monkeypatch.delenv("HF_INFERENCE_BASE_URL", raising=False)
        monkeypatch.delenv("MODEL_TINY", raising=False)
        router = ModelRouter(offline=False)
        provider = router.for_profile("hf:katanemo/Arch-Router-1.5B")
        assert isinstance(provider, LiteLLMProvider)
        assert provider.model == "openai/katanemo/Arch-Router-1.5B:hf-inference"
        assert provider.api_base == "https://router.huggingface.co/v1"
        assert provider.api_key == "hf_secret"
        assert provider.max_tokens == 192  # tiny tier decoding (the model's tier)

    def test_offline_hf_endpoint_key_serves_distinct_stub(self):
        # Offline, an HF key routes like any profile: the deterministic stub with the
        # key folded into the variant, so the choice still varies output reproducibly.
        router = ModelRouter(offline=True)
        provider = router.for_profile("hf:Qwen/Qwen2.5-7B-Instruct")
        assert isinstance(provider, DeterministicTinyModel)
        assert "hf:Qwen/Qwen2.5-7B-Instruct" in provider.variant


class TestFromEnv:
    def test_live_without_binding(self, monkeypatch):
        # No offline auto-detection: from_env always builds the live path, even with
        # no backend configured (the stub is reachable only via offline=True).
        for var in (
            "MODAL_WORKSPACE",
            "MODAL_LLM_BASE_URL",
            "OPENAI_API_KEY",
            "HF_TOKEN",
            "HUGGINGFACEHUB_API_TOKEN",
            "HF_INFERENCE_BASE_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        router = ModelRouter.from_env()
        assert router.offline is False
        assert not isinstance(router.for_profile("fast"), DeterministicTinyModel)

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
