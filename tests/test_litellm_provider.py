"""LiteLLM gateway tests — fully offline, litellm.completion monkeypatched.

No network and no real credentials: a fake ``litellm`` module (and a fake
response with ``.usage`` and a cost hook) is injected so we can assert the
provider returns the text and captures tokens + real cost, and that the router
builds a :class:`LiteLLMProvider` when live and the deterministic stub offline.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest

from src.models.litellm_provider import LiteLLMProvider
from src.models.provider import DeterministicTinyModel
from src.models.router import ModelRouter, ProfileSpec


# ── fake litellm response objects ────────────────────────────────────────────


@dataclass
class _FakeUsage:
    prompt_tokens: int = 11
    completion_tokens: int = 7
    total_tokens: int = 18


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str, *, hidden_cost: float | None = None) -> None:
        self.choices = [_FakeChoice(content)]
        self.usage = _FakeUsage()
        self._hidden_params = {} if hidden_cost is None else {"response_cost": hidden_cost}


def _install_fake_litellm(monkeypatch, *, response, cost_value=0.0, record=None):
    """Inject a fake ``litellm`` module exposing completion + completion_cost."""
    fake = types.ModuleType("litellm")

    def _completion(**kwargs):
        if record is not None:
            record.update(kwargs)
        if isinstance(response, Exception):
            raise response
        return response

    def _completion_cost(completion_response=None, **_kwargs):
        return cost_value

    fake.completion = _completion
    fake.completion_cost = _completion_cost
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return fake


# ── provider ─────────────────────────────────────────────────────────────────


class TestLiteLLMProviderComplete:
    def test_returns_text_and_captures_usage(self, monkeypatch):
        _install_fake_litellm(monkeypatch, response=_FakeResponse("a mossy booth"), cost_value=0.0)
        provider = LiteLLMProvider(model="openai/some/model", api_base="https://x/v1")
        out = provider.complete("scene-whisperer", "grow the wood")
        assert out == "a mossy booth"
        assert provider.last_usage["prompt_tokens"] == 11
        assert provider.last_usage["completion_tokens"] == 7
        assert provider.last_usage["total_tokens"] == 18

    def test_captures_cost_from_completion_cost(self, monkeypatch):
        _install_fake_litellm(monkeypatch, response=_FakeResponse("hi"), cost_value=0.0123)
        provider = LiteLLMProvider(model="openai/some/model", api_base="https://x/v1")
        provider.complete("echo", "drop a pebble")
        assert provider.last_usage["cost_usd"] == pytest.approx(0.0123)
        assert provider.last_cost == pytest.approx(0.0123)

    def test_prefers_hidden_params_cost(self, monkeypatch):
        # When LiteLLM already attached a cost, use it without re-pricing.
        _install_fake_litellm(monkeypatch, response=_FakeResponse("hi", hidden_cost=0.05), cost_value=999.0)
        provider = LiteLLMProvider(model="openai/some/model", api_base="https://x/v1")
        provider.complete("echo", "drop a pebble")
        assert provider.last_usage["cost_usd"] == pytest.approx(0.05)

    def test_calls_openai_style_for_custom_endpoint(self, monkeypatch):
        record: dict = {}
        _install_fake_litellm(monkeypatch, response=_FakeResponse("ok"), record=record)
        provider = LiteLLMProvider(
            model="openai/google/gemma-4-12B",
            api_base="https://ws--gemma-4-12b.modal.run/v1",
            api_key="EMPTY",
            temperature=0.3,
            max_tokens=99,
        )
        provider.complete("seedkeeper", "observe")
        assert record["model"] == "openai/google/gemma-4-12B"
        assert record["api_base"] == "https://ws--gemma-4-12b.modal.run/v1"
        assert record["api_key"] == "EMPTY"
        assert record["temperature"] == 0.3
        assert record["max_tokens"] == 99
        # Two messages: a role-derived system prompt, then the user prompt.
        roles = [m["role"] for m in record["messages"]]
        assert roles == ["system", "user"]
        assert record["messages"][1]["content"] == "observe"

    def test_defaults_api_key_for_custom_endpoint(self, monkeypatch):
        record: dict = {}
        _install_fake_litellm(monkeypatch, response=_FakeResponse("ok"), record=record)
        provider = LiteLLMProvider(model="openai/m", api_base="https://x/v1")  # no api_key
        provider.complete("echo", "x")
        assert record["api_key"] == "EMPTY"

    def test_error_returns_marker_and_zeroes_usage(self, monkeypatch):
        _install_fake_litellm(monkeypatch, response=RuntimeError("boom"))
        provider = LiteLLMProvider(model="openai/m", api_base="https://x/v1")
        out = provider.complete("echo", "x")
        assert out.startswith("[model error:")
        assert provider.last_usage["total_tokens"] == 0
        assert provider.last_usage["cost_usd"] == 0.0
        assert provider.last_cost == 0.0


# ── router integration ───────────────────────────────────────────────────────


class TestRouterBuildsGateway:
    def test_live_profile_builds_litellm_provider(self):
        router = ModelRouter(
            offline=False,
            specs={
                "fast": ProfileSpec(
                    model="openai/openbmb/MiniCPM4.1-8B",
                    base_url="https://ws--minicpm-4-1-8b.modal.run/v1",
                    api_key="EMPTY",
                )
            },
        )
        provider = router.for_profile("fast")
        assert isinstance(provider, LiteLLMProvider)
        assert provider.model == "openai/openbmb/MiniCPM4.1-8B"
        assert provider.api_base == "https://ws--minicpm-4-1-8b.modal.run/v1"

    def test_offline_builds_deterministic_stub(self):
        router = ModelRouter(offline=True)
        assert isinstance(router.for_profile("fast"), DeterministicTinyModel)

    def test_offline_usage_has_no_cost(self):
        # The offline stub never reports cost; the conductor reads 0.0 for it.
        router = ModelRouter(offline=True)
        provider = router.for_profile("tiny")
        provider.complete("scene-whisperer", "grow")
        assert "cost_usd" not in provider.last_usage


class _Msg:
    def __init__(self, content, **extra):
        self.content = content
        for k, v in extra.items():
            setattr(self, k, v)


def _resp(msg):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])


class TestReasoningCapture:
    """vLLM reasoning parsers (gemma4/qwen3) split the model's thinking into
    ``reasoning_content``; we capture it for the mind-reader, never re-prompt with it."""

    def test_extracts_reasoning_content(self):
        resp = _resp(_Msg("A dark brew warms the morning.", reasoning_content="I am the spy, stay vague"))
        assert LiteLLMProvider._extract_reasoning(resp) == "I am the spy, stay vague"

    def test_falls_back_to_provider_specific_fields(self):
        resp = _resp(_Msg("answer", provider_specific_fields={"reasoning": "hidden thinking"}))
        assert LiteLLMProvider._extract_reasoning(resp) == "hidden thinking"

    def test_empty_for_non_reasoning_model(self):
        assert LiteLLMProvider._extract_reasoning(_resp(_Msg("just an answer"))) == ""
