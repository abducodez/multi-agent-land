"""Open Table — a minimal, live-ready 2–3 agent conversation scenario.

Proves the scenario and its cast load from the auto-discovering registry, build,
reset, and that a few conductor ticks produce real ``agent.spoke`` events carrying
the say-vs-think ``text``/``thought``/``mood`` the Fishbowl UI renders — offline, with
no API key (deterministic stub, ADR-0021).  Zero mocks, per repo convention.
"""

from __future__ import annotations

from src.agents.base import ManifestAgent
from src.core.conductor import Conductor
from src.core.ledger_factory import make_ledger
from src.core.registry import default_registry


def _build_conductor(steps: int = 6) -> Conductor:
    reg = default_registry()
    c = Conductor(
        reg.build_scenario("open-table"),
        governor=reg.governor_for("open-table"),
        ledger=make_ledger(),
    )
    c.reset(c.scenario.default_seed)
    c.step(n_ticks=steps)
    return c


class TestOpenTableRegistry:
    def test_scenario_and_cast_are_discovered(self):
        reg = default_registry()
        assert "open-table" in reg.scenarios
        assert {"chat-curious", "chat-skeptic", "chat-host"} <= set(reg.agents)

    def test_scenario_builds_with_three_manifest_agents(self):
        reg = default_registry()
        sc = reg.build_scenario("open-table")
        assert [a.name for a in sc.agents] == ["chat-curious", "chat-skeptic", "chat-host"]
        assert all(isinstance(a, ManifestAgent) for a in sc.agents)
        assert sc.goal

    def test_profiles_and_ticks_read_from_config(self):
        reg = default_registry()
        sc = reg.build_scenario("open-table")
        by_name = {a.name: a.manifest for a in sc.agents}
        assert by_name["chat-curious"].model_profile == "fast"
        assert by_name["chat-skeptic"].model_profile == "balanced"
        assert by_name["chat-host"].model_profile == "fast"
        assert by_name["chat-curious"].schedule.tick_every == 1
        assert by_name["chat-skeptic"].schedule.tick_every == 1
        assert by_name["chat-host"].schedule.tick_every == 3

    def test_governor_uses_modest_live_safe_caps(self):
        reg = default_registry()
        gov = reg.governor_for("open-table")
        assert gov.max_turns == 40
        assert gov.max_total_calls == 400


class TestOpenTableConversation:
    def test_reset_writes_genesis_with_seed(self):
        c = _build_conductor(steps=0)
        text = " ".join(str(e.payload) for e in c.ledger.events)
        assert c.scenario.default_seed in text

    def test_ticks_produce_spoke_events_with_text(self):
        c = _build_conductor()
        spoke = [e for e in c.ledger.events if e.kind == "agent.spoke"]
        assert spoke, "the talkers should speak within a few ticks"
        assert all(e.payload.get("text") for e in spoke)

    def test_talkers_carry_thought_and_mood(self):
        c = _build_conductor()
        for actor in ("chat-curious", "chat-skeptic"):
            said = [e for e in c.ledger.events if e.kind == "agent.spoke" and e.actor == actor]
            assert said, f"{actor} (tick_every=1) should speak within a few ticks"
            payload = said[-1].payload
            assert payload.get("thought"), "the say-vs-think thought must be in the ledger offline"
            assert payload.get("mood"), "the mood must be in the ledger offline"
            assert payload.get("_raw_fallback") is None, "structured output should be clean offline"

    def test_host_carries_mood_but_no_thought(self):
        # The host opted into [mood] only, so it should not leak a thought field.
        c = _build_conductor(steps=6)
        host = [e for e in c.ledger.events if e.kind == "agent.spoke" and e.actor == "chat-host"]
        assert host, "chat-host (tick_every=3) should speak within a few ticks"
        payload = host[-1].payload
        assert payload.get("mood")
        assert "thought" not in payload
