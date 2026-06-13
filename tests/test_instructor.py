"""Validated structured-output tests — fully offline, instructor + litellm faked.

No network and no real credentials.  Three layers are covered:

  * ``build_output_model`` is pure Pydantic: it constrains ``kind`` to the
    allowed grant and requires ``text`` (+ extra fields).
  * ``LiteLLMProvider.complete_structured`` wraps a faked
    ``instructor.from_litellm`` client and reads tokens + cost from the raw
    completion, mirroring ``complete``.
  * ``ManifestAgent`` takes the structured path when the provider offers
    ``complete_structured`` (validated payload, no ``_raw_fallback``) and the
    tolerant-parser path with the deterministic stub.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ValidationError

from src.agents.base import ManifestAgent
from src.core.manifest import AgentManifest
from src.core.projections import StageProjection
from src.core.structured import AgentOutputError, build_output_model
from src.models.litellm_provider import LiteLLMProvider
from src.models.router import ModelRouter


# ── build_output_model (pure Pydantic) ─────────────────────────────────────────


class TestBuildOutputModel:
    def test_accepts_valid_kind(self):
        model = build_output_model(["agent.spoke", "judge.verdict"])
        out = model(kind="agent.spoke", text="I collect echoes.")
        assert out.kind == "agent.spoke"
        assert out.text == "I collect echoes."

    def test_rejects_kind_not_in_allowed(self):
        model = build_output_model(["agent.spoke"])
        with pytest.raises(ValidationError):
            model(kind="not.real", text="oops")

    def test_single_kind_still_constrains(self):
        model = build_output_model(["world.observed"])
        assert model(kind="world.observed", text="A booth opens.").kind == "world.observed"
        with pytest.raises(ValidationError):
            model(kind="judge.verdict", text="x")

    def test_extra_fields_required(self):
        model = build_output_model(["agent.spoke"], ["emotion"])
        out = model(kind="agent.spoke", text="hi", emotion="puzzled")
        assert out.emotion == "puzzled"
        with pytest.raises(ValidationError):
            model(kind="agent.spoke", text="hi")  # emotion missing

    def test_text_required(self):
        model = build_output_model(["agent.spoke"])
        with pytest.raises(ValidationError):
            model(kind="agent.spoke")

    def test_empty_allowed_kinds_raises(self):
        with pytest.raises(AgentOutputError):
            build_output_model([])

    def test_is_subclass_of_basemodel(self):
        model = build_output_model(["agent.spoke"])
        assert issubclass(model, BaseModel)


# ── fake instructor + litellm for the provider ──────────────────────────────────


@dataclass
class _FakeUsage:
    prompt_tokens: int = 11
    completion_tokens: int = 7
    total_tokens: int = 18


class _FakeRawCompletion:
    """Raw completion Instructor returns alongside the parsed model."""

    def __init__(self, *, hidden_cost: float | None = None) -> None:
        self.usage = _FakeUsage()
        self._hidden_params = {} if hidden_cost is None else {"response_cost": hidden_cost}


class _FakeInstructorClient:
    def __init__(self, *, hidden_cost=None, raise_exc=None, record=None) -> None:
        self._hidden_cost = hidden_cost
        self._raise = raise_exc
        self._record = record

    def create_with_completion(self, *, response_model, **kwargs):
        if self._record is not None:
            self._record.update(kwargs)
            self._record["response_model"] = response_model
        if self._raise is not None:
            raise self._raise
        # Instructor returns a validated instance of the requested model.
        result = response_model(kind=response_model.model_fields["kind"].annotation.__args__[0], text="a mossy booth")
        return result, _FakeRawCompletion(hidden_cost=self._hidden_cost)


def _install_fakes(monkeypatch, *, client, from_litellm_kw: dict | None = None) -> None:
    """Inject fake ``instructor`` (from_litellm -> client) and ``litellm`` modules.

    *from_litellm_kw*, when given, records the kwargs ``complete_structured`` passes to
    ``instructor.from_litellm`` (e.g. the chosen ``mode``) for assertion.
    """
    fake_litellm = types.ModuleType("litellm")
    fake_litellm.completion = lambda **kw: None

    def _completion_cost(completion_response=None, **_kw):
        return 0.0

    fake_litellm.completion_cost = _completion_cost

    def _from_litellm(completion, **kw):
        if from_litellm_kw is not None:
            from_litellm_kw.update(kw)
        return client

    fake_instructor = types.ModuleType("instructor")
    fake_instructor.from_litellm = _from_litellm
    # Mode is an enum on the real package; a name->value stand-in is enough for the
    # provider's ``getattr(instructor.Mode, structured_mode.upper())`` resolution.
    fake_instructor.Mode = types.SimpleNamespace(JSON_SCHEMA="json_schema", JSON="json", TOOLS="tools")

    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)
    monkeypatch.setitem(sys.modules, "instructor", fake_instructor)


# ── provider.complete_structured ────────────────────────────────────────────────


class TestCompleteStructured:
    def test_returns_validated_model_and_captures_usage(self, monkeypatch):
        _install_fakes(monkeypatch, client=_FakeInstructorClient())
        provider = LiteLLMProvider(model="openai/m", api_base="https://x/v1")
        model = build_output_model(["world.observed"])
        out = provider.complete_structured("scene-whisperer", "grow the wood", model)
        assert isinstance(out, model)
        assert out.kind == "world.observed"
        assert provider.last_usage["prompt_tokens"] == 11
        assert provider.last_usage["completion_tokens"] == 7
        assert provider.last_usage["total_tokens"] == 18

    def test_captures_cost_from_hidden_params(self, monkeypatch):
        _install_fakes(monkeypatch, client=_FakeInstructorClient(hidden_cost=0.05))
        provider = LiteLLMProvider(model="openai/m", api_base="https://x/v1")
        provider.complete_structured("echo", "drop a pebble", build_output_model(["agent.spoke"]))
        assert provider.last_usage["cost_usd"] == pytest.approx(0.05)
        assert provider.last_cost == pytest.approx(0.05)

    def test_passes_response_model_and_retries(self, monkeypatch):
        record: dict = {}
        _install_fakes(monkeypatch, client=_FakeInstructorClient(record=record))
        provider = LiteLLMProvider(model="openai/m", api_base="https://x/v1", max_retries=4)
        model = build_output_model(["world.observed"])
        provider.complete_structured("seedkeeper", "observe", model)
        assert record["response_model"] is model
        assert record["max_retries"] == 4
        assert record["model"] == "openai/m"
        roles = [m["role"] for m in record["messages"]]
        assert roles == ["system", "user"]

    def test_defaults_to_guided_json_schema_mode(self, monkeypatch):
        # Guided decoding, not tool calling: a model with no tool-call parser (e.g. MiniCPM)
        # still validates instead of 400ing. The mode rides on from_litellm, not the call.
        kw: dict = {}
        _install_fakes(monkeypatch, client=_FakeInstructorClient(), from_litellm_kw=kw)
        provider = LiteLLMProvider(model="openai/m", api_base="https://x/v1")
        provider.complete_structured("echo", "x", build_output_model(["agent.spoke"]))
        assert kw["mode"] == "json_schema"

    def test_structured_mode_override_is_honored(self, monkeypatch):
        kw: dict = {}
        _install_fakes(monkeypatch, client=_FakeInstructorClient(), from_litellm_kw=kw)
        provider = LiteLLMProvider(model="openai/m", api_base="https://x/v1", structured_mode="tools")
        provider.complete_structured("echo", "x", build_output_model(["agent.spoke"]))
        assert kw["mode"] == "tools"

    def test_error_zeroes_usage_and_reraises(self, monkeypatch):
        _install_fakes(monkeypatch, client=_FakeInstructorClient(raise_exc=RuntimeError("boom")))
        provider = LiteLLMProvider(model="openai/m", api_base="https://x/v1")
        with pytest.raises(RuntimeError):
            provider.complete_structured("echo", "x", build_output_model(["agent.spoke"]))
        assert provider.last_usage["total_tokens"] == 0
        assert provider.last_usage["cost_usd"] == 0.0


# ── ManifestAgent path selection ────────────────────────────────────────────────


class _Agent(ManifestAgent):
    manifest = AgentManifest(
        name="scene-whisperer",
        persona="You grow the wood in one strange sentence.",
        may_emit=["world.observed"],
        model_profile="tiny",
    )


@dataclass
class _StructuredProvider:
    """Stand-in live provider exposing complete_structured."""

    last_usage: dict = None  # type: ignore[assignment]
    seen_model: object = None

    def __post_init__(self):
        self.last_usage = {
            "prompt_tokens": 5,
            "completion_tokens": 3,
            "total_tokens": 8,
            "cost_usd": 0.002,
        }

    def complete_structured(self, role, prompt, response_model):
        self.seen_model = response_model
        return response_model(kind="world.observed", text="A booth opens in a root.")

    def complete(self, role, prompt):  # pragma: no cover - must not be reached
        raise AssertionError("structured path must not call complete()")


@dataclass
class _FixedRouter:
    provider: object

    def for_profile(self, profile):
        return self.provider


class TestManifestAgentStructuredPath:
    def test_uses_structured_path_when_available(self):
        provider = _StructuredProvider()
        agent = _Agent(_FixedRouter(provider))
        ev = agent.act("r", 1, StageProjection(seed="moss"), ())
        assert ev.kind == "world.observed"
        assert ev.payload["text"] == "A booth opens in a root."
        # The validated path never wraps prose, so no fallback marker is present.
        assert "_raw_fallback" not in ev.payload
        # Cost/tokens flowed through from the provider for the Governor.
        assert agent.last_usage["cost_usd"] == pytest.approx(0.002)
        assert agent.last_usage["total_tokens"] == 8
        # The constructed model was constrained to the manifest's may_emit.
        assert provider.seen_model.model_fields["kind"].annotation.__args__ == ("world.observed",)

    def test_deterministic_stub_uses_parser_path(self):
        # Offline router yields the stub, which has no complete_structured: the
        # tolerant parser runs and (for prose) marks the fallback.
        agent = _Agent(ModelRouter(offline=True))
        provider = agent.router.for_profile("tiny")
        assert not hasattr(provider, "complete_structured")
        ev = agent.act("r", 1, StageProjection(seed="moss"), ())
        assert ev.kind == "world.observed"  # coerced to the only allowed kind
        assert ev.payload.get("_raw_fallback") is True
        assert agent.last_usage["total_tokens"] > 0

    def test_structured_failure_falls_back_to_parser(self):
        # If the live structured call raises, the agent still produces an event
        # via the parser path rather than dropping the turn.
        class _FailingProvider:
            def __init__(self):
                self.last_usage = {"total_tokens": 0, "cost_usd": 0.0}
                self.calls = []

            def complete_structured(self, role, prompt, response_model):
                raise RuntimeError("validation exhausted")

            def complete(self, role, prompt):
                self.calls.append(prompt)
                self.last_usage = {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                    "cost_usd": 0.0,
                }
                return '{"kind": "world.observed", "text": "fallback line"}'

        provider = _FailingProvider()
        agent = _Agent(_FixedRouter(provider))
        ev = agent.act("r", 1, StageProjection(), ())
        assert ev.kind == "world.observed"
        assert ev.payload["text"] == "fallback line"
        assert provider.calls, "fallback should call complete()"
        assert agent.last_usage["total_tokens"] == 6
