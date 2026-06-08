from __future__ import annotations

import pytest

from src.agents.base import ManifestAgent
from src.core.events import Event
from src.core.manifest import AgentManifest, MemoryConfig, ScheduleConfig
from src.core.projections import StageProjection, rebuild_stage
from src.core.structured import AgentOutputError
from src.models.router import ModelRouter


def _router() -> ModelRouter:
    return ModelRouter(offline=True)


def _events(*kinds: str) -> tuple[Event, ...]:
    return tuple(
        Event(run_id="r", turn=i, kind=k, actor="scene-whisperer", payload={"text": f"line {i}"})
        for i, k in enumerate(kinds, start=1)
    )


class _Seedkeeper(ManifestAgent):
    manifest = AgentManifest(
        name="scene-whisperer",
        role="worker",
        persona="You grow the wood in one strange sentence.",
        subscribes_to=["run.started", "user.injected"],
        may_emit=["world.observed"],
        schedule=ScheduleConfig(tick_every=1),
        model_profile="tiny",
        memory=MemoryConfig(window=6),
    )


class _Reflector(ManifestAgent):
    manifest = AgentManifest(
        name="scene-whisperer",
        role="worker",
        persona="You remember and believe.",
        may_emit=["world.observed"],
        model_profile="fast",
        memory=MemoryConfig(window=6, reflection_threshold=3),
    )


class _SalienceKeeper(ManifestAgent):
    manifest = AgentManifest(
        name="scene-whisperer",
        role="worker",
        persona="You recall what matters.",
        may_emit=["world.observed"],
        model_profile="balanced",
        memory=MemoryConfig(window=4, use_salience=True, salience_top_k=3),
    )


class TestManifestAgentEmits:
    def test_emits_allowed_kind(self):
        agent = _Seedkeeper(_router())
        ev = agent.act("r", 1, StageProjection(seed="moss"), _events("run.started"))
        assert ev.kind == "world.observed"  # only allowed content kind
        assert ev.actor == "scene-whisperer"

    def test_records_token_usage(self):
        agent = _Seedkeeper(_router())
        agent.act("r", 1, StageProjection(seed="moss"), ())
        assert agent.last_usage["total_tokens"] > 0

    def test_routes_by_profile(self):
        # tiny profile -> the tiny stub variant
        agent = _Seedkeeper(router := _router())
        agent.act("r", 1, StageProjection(), ())
        assert router.for_profile("tiny").variant == "stub:tiny"

    def test_model_endpoint_overrides_profile(self):
        # An explicit catalogue endpoint pins a specific model: _route_key prefers it
        # over the tier, and the agent routes there (ADR-0022).
        class _Pinned(ManifestAgent):
            manifest = AgentManifest(
                name="scene-whisperer",
                role="worker",
                persona="You speak on a specific model.",
                may_emit=["world.observed"],
                model_profile="tiny",
                model_endpoint="minicpm-4-1-8b",
            )

        agent = _Pinned(router := _router())
        assert agent._route_key == "minicpm-4-1-8b"
        agent.act("r", 1, StageProjection(), ())
        # the routed (cached) provider is the endpoint's stub, not the tiny tier's
        assert router.for_profile("minicpm-4-1-8b").variant == "stub:minicpm-4-1-8b"


class TestManifestAgentSalience:
    def test_salience_path_runs(self):
        agent = _SalienceKeeper(_router())
        events = _events("world.observed", "world.observed", "judge.verdict")
        ev = agent.act("r", 4, rebuild_stage(events), events)
        assert ev.kind == "world.observed"


class TestManifestAgentReflection:
    def test_reflection_fires_at_threshold(self):
        agent = _Reflector(_router())
        # three globally-visible events -> tracker due at threshold 3
        events = _events("world.observed", "world.observed", "world.observed")
        ev = agent.act("r", 3, rebuild_stage(events), events)
        assert ev.kind == "agent.reflected"

    def test_no_reflection_below_threshold(self):
        agent = _Reflector(_router())
        events = _events("world.observed", "world.observed")
        ev = agent.act("r", 2, rebuild_stage(events), events)
        assert ev.kind == "world.observed"

    def test_content_kinds_exclude_reflection(self):
        agent = _Reflector(_router())
        assert "agent.reflected" not in agent._content_kinds()
        assert agent._content_kinds() == ["world.observed"]


class _Provider:
    def __init__(self, reasoning: str = "") -> None:
        self.last_reasoning = reasoning


class TestThoughtFromReasoning:
    """The mind-reader ``thought`` is filled from the model's reasoning only when the
    agent wants a thought and the model gave none (the fallback path)."""

    def test_fills_thought_from_provider_reasoning(self):
        out = ManifestAgent._with_reasoning({"text": "clue"}, _Provider("the real thinking"), "", True)
        assert out["thought"] == "the real thinking"

    def test_does_not_override_an_explicit_thought(self):
        out = ManifestAgent._with_reasoning({"text": "clue", "thought": "given"}, _Provider("other"), "", True)
        assert out["thought"] == "given"

    def test_skips_when_agent_wants_no_thought(self):
        out = ManifestAgent._with_reasoning({"text": "clue"}, _Provider("ignored"), "", False)
        assert "thought" not in out

    def test_falls_back_to_inline_think_tags(self):
        out = ManifestAgent._with_reasoning({"text": "x"}, _Provider(""), "<think>inline plan</think> x", True)
        assert out["thought"] == "inline plan"


class _FakeProvider:
    """A live-style provider: returns canned prose and a reasoning channel."""

    def __init__(self, text: str, reasoning: str = "") -> None:
        self._text = text
        self.last_reasoning = reasoning
        self.last_usage = {"total_tokens": 3}

    def complete(self, role: str, prompt: str) -> str:
        return self._text


class TestProseFallback:
    """When live structured output fails we ask for a plain line, clean it, and skip
    the turn if nothing usable survives — never shipping `…`, junk, or a leaked word."""

    def test_returns_clean_clue_and_reasoning_thought(self):
        agent = _Seedkeeper(_router())
        out = agent._prose_fallback(
            "spy-cara", "P", ["agent.spoke"], True, _FakeProvider("A dark brew warms the dawn.", "I am the spy")
        )
        assert out == {"kind": "agent.spoke", "text": "A dark brew warms the dawn.", "thought": "I am the spy"}

    def test_skips_on_degenerate_output(self):
        agent = _Seedkeeper(_router())
        with pytest.raises(AgentOutputError):
            agent._prose_fallback("r", "P", ["agent.spoke"], False, _FakeProvider("…"))

    def test_skips_and_never_leaks_the_secret(self):
        agent = _Seedkeeper(_router())
        # The model named the secret while reasoning; the cleaned clue is empty → skip.
        with pytest.raises(AgentOutputError):
            agent._prose_fallback(
                "r", "P", ["agent.spoke"], False, _FakeProvider("Secret word is COFFEE. Need to output JSON.")
            )


class _FailingRouter:
    """Routes every profile to a provider whose call failed — it returns the
    ``[model error: …]`` sentinel instead of a line, as a live provider does on a
    transient connection drop."""

    def __init__(self, exc: object) -> None:
        from src.models.provider import model_error

        self._provider = _FakeProvider(model_error(exc))

    def for_profile(self, key: str):
        return self._provider


class TestModelErrorIsNeverSpoken:
    """A failed model call must never reach the stage as the agent's line. ``complete()``
    returns the failure sentinel (it can't raise — it returns ``str``); the agent turns it
    back into an :class:`AgentOutputError` so the conductor's resilient loop skips the turn
    and records it, rather than speaking the raw connection error (ADR-0023)."""

    _CONN_ERR = "litellm.InternalServerError: InternalServerError: OpenAIException - Connection error."

    def test_prose_fallback_raises_on_model_error(self):
        from src.models.provider import model_error

        agent = _Seedkeeper(_router())
        with pytest.raises(AgentOutputError) as exc:
            agent._prose_fallback(
                "scene-whisperer", "P", ["world.observed"], False, _FakeProvider(model_error(self._CONN_ERR))
            )
        assert "model call failed" in str(exc.value)

    def test_act_raises_so_the_loop_skips_the_turn(self):
        # End-to-end through act(): the routed provider's call failed, so the agent raises
        # rather than emitting an event whose text is the connection error.
        agent = _Seedkeeper(_FailingRouter(self._CONN_ERR))
        with pytest.raises(AgentOutputError):
            agent.act("r", 1, StageProjection(seed="moss"), _events("run.started"))


class TestRepeatGuard:
    """Conversation flow: a near-duplicate spoken line is skipped so the cast advances
    instead of echoing each other (the verbatim-repeat loop seen live)."""

    @staticmethod
    def _spoken(*texts: str) -> tuple[Event, ...]:
        return tuple(
            Event(run_id="r", turn=i, kind="agent.spoke", actor="a", payload={"text": t})
            for i, t in enumerate(texts, start=1)
        )

    def test_exact_repeat_is_caught(self):
        line = "The scent lingers long after brewing, almost like a memory."
        assert ManifestAgent._is_repeat(line, self._spoken(line))

    def test_a_distinct_line_passes(self):
        recent = self._spoken("The scent lingers long after brewing, almost like a memory.")
        assert not ManifestAgent._is_repeat("A bitter jolt that wakes the whole house at dawn.", recent)

    def test_only_spoken_kinds_are_compared(self):
        # A look-alike verdict in history must not block a fresh clue (different kind).
        recent = (Event(run_id="r", turn=1, kind="judge.verdict", actor="host", payload={"text": "A warm cup."}),)
        assert not ManifestAgent._is_repeat("A warm cup.", recent)

    def test_act_skips_a_live_repeat_but_offline_keeps_it(self):
        # The guard is live-only: through act() a LIVE router skips a verbatim repeat,
        # while the offline stub (reproducible, curated) keeps emitting.
        class _Prov:
            last_usage = {"total_tokens": 1}
            last_reasoning = ""

            def complete(self, role, prompt):
                return "A warm cup soothes the morning."

        class _LiveRouter:
            offline = False

            def for_profile(self, key):
                return _Prov()

        class _A(ManifestAgent):
            manifest = AgentManifest(
                name="a", persona="p", may_emit=["agent.spoke"], schedule=ScheduleConfig(tick_every=1)
            )

        prior = self._spoken("A warm cup soothes the morning.")
        with pytest.raises(AgentOutputError):
            _A(_LiveRouter()).act("r", 2, StageProjection(), prior)
        # Same repeat, but offline → emitted, never skipped (keeps demos/tests reproducible).
        ev = _A(_router()).act("r", 2, StageProjection(), prior)
        assert ev.kind == "agent.spoke"
