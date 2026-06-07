"""Fishbowl presenter — cast-state projection, adapter mapping, view-model snapshot.

The marquee proof is :class:`TestOfflineEmitsMoodAndThought`: with no API key, a real
conductor run produces a ledger that carries the say-vs-think ``thought``/``mood`` the
UI renders — so the mind-reader is genuinely model-driven offline (ADR-0021).  Zero
mocks, per the repo convention.
"""

from __future__ import annotations

from src.core.conductor import Conductor
from src.core.events import Event
from src.core.ledger_factory import make_ledger
from src.core.registry import default_registry
from src.tools.builtins import default_tool_registry
from src.ui.fishbowl import adapter, derive_cast_state, view_model_at


def _ev(kind: str, actor: str, turn: int = 1, **payload) -> Event:
    return Event(run_id="r", turn=turn, kind=kind, actor=actor, payload=payload)


class TestDeriveCastState:
    def test_spoke_with_thought_and_mood(self):
        events = (_ev("agent.spoke", "pocket-actor", text="I want the moon", thought="secretly scared", mood="panic"),)
        st = derive_cast_state(events, ["pocket-actor"])["pocket-actor"]
        assert st.said == "I want the moon"
        assert st.thought == "secretly scared"
        assert st.mood == "panic"
        assert st.spoke is True

    def test_thought_only_agent_has_no_said(self):
        events = (_ev("agent.thought", "echo", text="the wood holds its breath", mood="thinking"),)
        st = derive_cast_state(events, ["echo"])["echo"]
        assert st.thought == "the wood holds its breath"
        assert st.said is None
        assert st.mood == "thinking"

    def test_latest_wins_and_prefix_replay_is_pure(self):
        events = (
            _ev("agent.spoke", "pocket-actor", turn=1, text="first", mood="calm"),
            _ev("agent.spoke", "pocket-actor", turn=2, text="second", mood="smug"),
        )
        assert derive_cast_state(events, ["pocket-actor"])["pocket-actor"].said == "second"
        # scrub back to the prefix — deterministic, no mutation of the log
        assert derive_cast_state(events[:1], ["pocket-actor"])["pocket-actor"].said == "first"
        assert derive_cast_state(events[:1], ["pocket-actor"])["pocket-actor"].said == "first"

    def test_unknown_actor_is_ignored(self):
        states = derive_cast_state((_ev("agent.spoke", "stranger", text="hi"),), ["pocket-actor"])
        assert states["pocket-actor"].said is None
        assert "stranger" not in states

    def test_reflection_does_not_touch_said_or_thought(self):
        events = (_ev("agent.reflected", "scene-whisperer", text="I am patient"),)
        st = derive_cast_state(events, ["scene-whisperer"])["scene-whisperer"]
        assert st.said is None and st.thought is None


class TestAdapter:
    def test_hue_prefers_manifest_else_derives_stably(self):
        class WithHue:
            name, hue, archetype, role = "x", 42, None, "worker"

        class NoHue:
            name, hue, archetype, role = "echo", None, None, "worker"

        assert adapter.agent_hue(WithHue()) == 42
        h = adapter.agent_hue(NoHue())
        assert 0 <= h < 360 and adapter.agent_hue(NoHue()) == h  # stable

    def test_tier_mapping(self):
        assert adapter.model_tier("tiny") == "fast"
        assert adapter.model_tier("balanced") == "mid"
        assert adapter.model_tier("strong") == "deep"

    def test_mood_normalization(self):
        assert adapter.normalize_mood("panic") == "panic"
        assert adapter.normalize_mood("curious") == "calm"
        assert adapter.normalize_mood(None) == "calm"

    def test_feed_vocabulary(self):
        assert adapter.event_to_feed_item(_ev("world.observed", "sw", text="x"))["kind"] == "narrate"
        assert adapter.event_to_feed_item(_ev("user.injected", "visitor", text="x", label="GUST"))["label"] == "GUST"
        assert adapter.event_to_feed_item(_ev("user.injected", "visitor", text="x"))["label"] == "DISTURBANCE"
        assert adapter.event_to_feed_item(_ev("judge.verdict", "j", text="guilty"))["kind"] == "verdict"
        assert adapter.event_to_feed_item(_ev("run.started", "conductor", seed="s")) is None


class TestViewModel:
    def _events(self) -> tuple[Event, ...]:
        return (
            _ev("run.started", "conductor", turn=0, seed="seed", goal="g"),
            _ev("world.observed", "scene-whisperer", turn=1, text="the wood wakes"),
            _ev("agent.spoke", "pocket-actor", turn=2, text="I want the moon", thought="scared", mood="panic"),
            _ev("user.injected", "visitor", turn=3, text="a lantern hums", label="POKE"),
            _ev("judge.verdict", "mischief-critic", turn=4, text="keep it", mood="smug"),
        )

    def _cast(self):
        scenario = default_registry().build_scenario("thousand-token-wood", tools=default_tool_registry())
        return [a.manifest for a in scenario.agents]

    def test_snapshot_shape(self):
        events, cast = self._events(), self._cast()
        vm = view_model_at(events, len(events), cast, scenario_name="thousand-token-wood")
        assert vm["step"] == vm["total"] == len(events)
        assert vm["scene"] == "the wood wakes"
        pa = next(c for c in vm["cast"] if c["id"] == "pocket-actor")
        assert pa["said"] == "I want the moon" and pa["thought"] == "scared" and pa["mood"] == "panic"
        kinds = {f["kind"] for f in vm["feed"]}
        assert {"narrate", "say", "poke", "verdict"} <= kinds  # run.started omitted
        assert vm["verdict"]["text"] == "keep it"
        assert vm["rounds"] == 2  # one poke

    def test_prefix_is_clamped_and_tokens_grow(self):
        events, cast = self._events(), self._cast()
        vm0 = view_model_at(events, 0, cast)
        vm_all = view_model_at(events, 999, cast)  # clamps to len
        assert vm0["step"] == 0 and vm_all["step"] == len(events)
        assert vm_all["tokens"] >= vm0["tokens"]

    def test_speaking_id_tracks_the_head(self):
        events, cast = self._events(), self._cast()
        vm = view_model_at(events, 3, cast)  # head is pocket-actor's spoke
        assert vm["speaking_id"] == "pocket-actor"


class TestOfflineEmitsMoodAndThought:
    """With no API key the ledger itself carries the say-vs-think data (ADR-0021)."""

    def _run(self, scenario: str, steps: int = 6) -> Conductor:
        reg = default_registry()
        c = Conductor(
            reg.build_scenario(scenario, tools=default_tool_registry()),
            governor=reg.governor_for(scenario),
            ledger=make_ledger(),
        )
        c.reset(c.scenario.default_seed)
        c.step(n_ticks=steps)
        return c

    def test_pocket_actor_spoke_carries_thought_and_mood(self):
        c = self._run("thousand-token-wood")
        spoke = [e for e in c.ledger.events if e.kind == "agent.spoke" and e.actor == "pocket-actor"]
        assert spoke, "pocket-actor (tick_every=2) should speak within a few ticks"
        payload = spoke[-1].payload
        assert payload.get("thought"), "the say-vs-think thought must be in the ledger offline"
        assert payload.get("mood"), "the mood must be in the ledger offline"
        assert payload.get("_raw_fallback") is None, "structured output should be clean offline"

    def test_opt_out_agent_payload_has_no_extra_fields(self):
        c = self._run("thousand-token-wood")
        obs = [e for e in c.ledger.events if e.kind == "world.observed" and e.actor == "scene-whisperer"]
        assert obs
        # scene-whisperer declares no output_extra_fields → no thought/mood leak.
        assert "thought" not in obs[-1].payload and "mood" not in obs[-1].payload

    def test_view_model_from_a_live_offline_run(self):
        c = self._run("thousand-token-wood")
        cast = [a.manifest for a in c.scenario.agents]
        vm = view_model_at(
            c.ledger.events,
            len(c.ledger.events),
            cast,
            scenario_name="thousand-token-wood",
            governor=c.governor,
        )
        assert vm["cast"] and vm["tokens_real"] is not None
        # the mind-reader has something real to show: a thought and/or a vivid mood.
        assert any(c2["thought"] for c2 in vm["cast"]) or ({c2["mood"] for c2 in vm["cast"]} - {"calm"})
