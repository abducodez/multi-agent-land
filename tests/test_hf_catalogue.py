"""Tests for the Hugging Face inference catalogue (stdlib-only, offline-safe).

The catalogue is pure data + URL building: it loads with no token, every model stays
within the ≤32B "small minds" rule (tiny ≤4B), and a binding derives the LiteLLM
OpenAI-compatible string + the HF router URL + the token from the env.
"""

from __future__ import annotations

from src.models import hf_catalogue

# Tier upper bounds (billions of params) the catalogue must respect.
_TIER_CAP = {"tiny": 4, "fast": 8, "balanced": 13, "strong": 32}


def test_entries_load_offline_and_are_well_shaped():
    entries = hf_catalogue.entries()
    assert entries, "the HF catalogue should not be empty"
    for e in entries:
        assert {"key", "provider", "served_model_id", "profile", "params_b"} <= set(e)
        # The key is the repo id, and the served id matches it (router expects the repo id).
        assert e["key"] == e["served_model_id"]
        assert "/" in e["served_model_id"]


def test_every_model_is_within_its_tier_param_cap():
    for e in hf_catalogue.entries():
        tier, params = e["profile"], e["params_b"]
        assert params is not None and params <= 32  # the "small minds" rule
        if tier in _TIER_CAP:
            assert params <= _TIER_CAP[tier], f"{e['key']} ({params}B) exceeds {tier} cap"


def test_catalogue_has_a_tiny_default():
    # The catalogue is currently scoped to the one chat-capable model live on the
    # enabled providers (tagged tiny). Tiers without a dedicated model fall back to
    # it at the UI layer (see lab._default_model_key), so they may return None here.
    assert hf_catalogue.default_key_for_profile("tiny") == "katanemo/Arch-Router-1.5B"


def test_binding_uses_router_url_and_token():
    key = hf_catalogue.default_key_for_profile("tiny")
    binding = hf_catalogue.binding_for(key, env={"HF_TOKEN": "hf_xyz"})
    # The model pins its provider (hf-inference) so routing needs no paid auto-select.
    assert binding["model"] == f"openai/{key}:hf-inference"
    assert binding["base_url"] == hf_catalogue.DEFAULT_BASE_URL
    assert binding["api_key"] == "hf_xyz"


def test_binding_honours_explicit_base_url_and_legacy_token_var():
    key = hf_catalogue.default_key_for_profile("tiny")
    binding = hf_catalogue.binding_for(
        key,
        env={"HF_INFERENCE_BASE_URL": "https://my-tgi.example/v1", "HUGGINGFACEHUB_API_TOKEN": "legacy"},
    )
    assert binding["base_url"] == "https://my-tgi.example/v1"
    assert binding["api_key"] == "legacy"  # the older token var is accepted


def test_has_credentials():
    assert hf_catalogue.has_credentials({"HF_TOKEN": "x"}) is True
    assert hf_catalogue.has_credentials({"HF_INFERENCE_BASE_URL": "https://box/v1"}) is True
    assert hf_catalogue.has_credentials({}) is False


def test_unknown_key_raises():
    import pytest

    with pytest.raises(KeyError):
        hf_catalogue.binding_for("not/a-real-model", env={"HF_TOKEN": "x"})
