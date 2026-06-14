"""The Commentator (``rafters-critic``) — cadence, abstain, and the feed card.

Zero mocks: the offline ``DeterministicTinyModel`` (wired by ``conftest``) drives the
funny line, and we assert the per-speaker quorum gate, the self-trigger guard, offline
determinism, that a beat actually lands in a live show, and that the commentary renders
as a graceful feed card (with and without media).
"""

from __future__ import annotations

from src.core.conductor import Conductor
from src.core.events import Event
from src.core.ledger import Ledger
from src.core.projections import StageProjection
from src.core.registry import default_registry
from src.tools.builtins import default_tool_registry
from src.ui.fishbowl import view_model_at
from src.ui.fishbowl.render.feed import render_feed


def _ev(kind: str, actor: str, turn: int = 1, **payload) -> Event:
    return Event(run_id="r", turn=turn, kind=kind, actor=actor, payload=payload)


def _projection() -> StageProjection:
    return StageProjection(
        seed="a village of stage props wakes up", goal="grow the wood", current_scene="the wood hums"
    )


def _critic(cast_names: list[str]):
    """A live ``rafters-critic`` handler on the offline router, with a cast attached."""
    reg = default_registry()
    agent = reg.build_agent("rafters-critic", reg.build_router(), default_tool_registry())
    agent.cast_names = list(cast_names)
    return agent


class TestCadence:
    def test_abstains_below_count(self, monkeypatch):
        monkeypatch.setenv("MAL_COMMENTATOR_EVERY", "4")
        critic = _critic(["scene-whisperer", "pocket-actor", "rafters-critic"])
        # Only 2 speech beats so far → the cadence (4) is not met yet.
        events = (
            _ev("world.observed", "scene-whisperer", text="the wood hums"),
            _ev("agent.spoke", "pocket-actor", text="I want the moon"),
        )
        assert critic.act("r", 1, _projection(), events) is None

    def test_emits_one_beat_at_count(self, monkeypatch):
        monkeypatch.setenv("MAL_COMMENTATOR_EVERY", "4")
        critic = _critic(["scene-whisperer", "pocket-actor", "rafters-critic"])
        # 4 speech beats since the last remark → chime in exactly once.
        events = (
            _ev("world.observed", "scene-whisperer", text="a"),
            _ev("agent.spoke", "pocket-actor", text="b"),
            _ev("world.observed", "scene-whisperer", text="c"),
            _ev("agent.spoke", "pocket-actor", text="d"),
        )
        event = critic.act("r", 5, _projection(), events)
        assert event is not None
        assert event.kind == "commentary.posted"
        assert event.payload.get("text")

    def test_one_silent_speaker_does_not_wedge_cadence(self, monkeypatch):
        """A stalled speaker can't block the beat — the old per-speaker quorum bug."""
        monkeypatch.setenv("MAL_COMMENTATOR_EVERY", "4")
        critic = _critic(["scene-whisperer", "pocket-actor", "rafters-critic"])
        # pocket-actor spoke once then went silent (errored out); scene-whisperer carries
        # the show. A count-based cadence still fires; a per-speaker quorum never would.
        events = (
            _ev("agent.spoke", "pocket-actor", text="I want the moon"),
            _ev("world.observed", "scene-whisperer", text="a"),
            _ev("world.observed", "scene-whisperer", text="b"),
            _ev("world.observed", "scene-whisperer", text="c"),
        )
        event = critic.act("r", 5, _projection(), events)
        assert event is not None and event.kind == "commentary.posted"

    def test_no_speakers_means_silence(self, monkeypatch):
        monkeypatch.setenv("MAL_COMMENTATOR_EVERY", "1")
        critic = _critic(["scene-whisperer", "pocket-actor", "rafters-critic"])
        # Only the critic's own / non-speech events exist → nobody to comment on.
        events = (_ev("run.started", "conductor", turn=0, seed="s"),)
        assert critic.act("r", 1, _projection(), events) is None

    def test_window_resets_after_a_remark(self, monkeypatch):
        """The self-trigger guard: a posted beat resets the cadence window."""
        monkeypatch.setenv("MAL_COMMENTATOR_EVERY", "4")
        critic = _critic(["scene-whisperer", "pocket-actor", "rafters-critic"])
        events = (
            _ev("world.observed", "scene-whisperer", text="a"),
            _ev("agent.spoke", "pocket-actor", text="b"),
            _ev("world.observed", "scene-whisperer", text="c"),
            _ev("agent.spoke", "pocket-actor", text="d"),
            _ev("commentary.posted", "rafters-critic", text="bold choice, mushrooms"),
        )
        # No NEW speech since the remark → abstain again (no runaway self-trigger).
        assert critic.act("r", 6, _projection(), events) is None

    def test_offline_summary_is_deterministic(self, monkeypatch):
        monkeypatch.setenv("MAL_COMMENTATOR_EVERY", "1")
        events = (
            _ev("world.observed", "scene-whisperer", text="a"),
            _ev("agent.spoke", "pocket-actor", text="b"),
        )
        a = _critic(["scene-whisperer", "pocket-actor"]).act("r", 1, _projection(), events)
        b = _critic(["scene-whisperer", "pocket-actor"]).act("r", 1, _projection(), events)
        assert a is not None and b is not None
        assert a.payload["text"] == b.payload["text"]


class TestModularity:
    def test_beat_lands_in_a_live_thousand_token_wood_show(self):
        """Drop-in agent, no engine edit: a real offline run yields a commentary beat."""
        reg = default_registry()
        scenario = reg.build_scenario("thousand-token-wood", tools=default_tool_registry())
        conductor = Conductor(scenario, governor=reg.governor_for("thousand-token-wood"), ledger=Ledger())
        conductor.reset("a village of stage props wakes up")
        conductor.step(8)  # default cadence (4 beats, ~2 turns) trips early in the run
        kinds = [e.kind for e in conductor.ledger.events_for_run(conductor.run_id)]
        assert "commentary.posted" in kinds

    def test_scenario_without_critic_has_no_commentary(self):
        reg = default_registry()
        scenario = reg.build_scenario("mystery-roots", tools=default_tool_registry())
        conductor = Conductor(scenario, governor=reg.governor_for("mystery-roots"), ledger=Ledger())
        conductor.reset("who moved the standing stone?")
        conductor.step(6)
        kinds = {e.kind for e in conductor.ledger.events_for_run(conductor.run_id)}
        assert not any(k.startswith("commentary.") for k in kinds)


class TestFeedCard:
    def _cast(self):
        scenario = default_registry().build_scenario("thousand-token-wood", tools=default_tool_registry())
        return [a.manifest for a in scenario.agents]

    def test_renders_media_badges_not_inline_tags(self):
        """Media now plays in the native gr.Image/gr.Audio cutaway, so the feed card shows
        badges — not inline <img>/<audio> (those used a /file= route dead in Gradio 5+)."""
        events = (
            _ev("run.started", "conductor", turn=0, seed="s", goal="g"),
            _ev(
                "commentary.posted",
                "rafters-critic",
                turn=3,
                text="bold choice, unionising the mushrooms",
                image={"src": "/file=runs/media/r/003-img.png", "alt": "the vision"},
                audio={"src": "data:audio/wav;base64,UklGRg==", "mime": "audio/wav"},
            ),
        )
        vm = view_model_at(events, 999, self._cast(), scenario_name="thousand-token-wood")
        html = render_feed(vm, mind_reader=False)
        assert "fe commentate" in html
        assert "FROM THE RAFTERS" in html
        assert "bold choice, unionising the mushrooms" in html
        # Badges note the media; the media itself is rendered by the native cutaway.
        assert "cm-badge" in html and "illustrated" in html and "voiced" in html
        # No dead inline media tags / hand-built /file= URLs.
        assert "<img" not in html and "<audio" not in html
        assert "/file=" not in html

    def test_degrades_to_text_only(self):
        events = (
            _ev("run.started", "conductor", turn=0, seed="s", goal="g"),
            _ev("commentary.posted", "rafters-critic", turn=3, text="just the line, no pictures"),
        )
        vm = view_model_at(events, 999, self._cast(), scenario_name="thousand-token-wood")
        html = render_feed(vm, mind_reader=False)
        assert "fe commentate" in html
        assert "just the line, no pictures" in html
        assert "<img" not in html
        assert "<audio" not in html

    def test_escapes_caption(self):
        events = (
            _ev("run.started", "conductor", turn=0, seed="s", goal="g"),
            _ev("commentary.posted", "rafters-critic", turn=3, text="a <script> & friends"),
        )
        vm = view_model_at(events, 999, self._cast(), scenario_name="thousand-token-wood")
        html = render_feed(vm, mind_reader=False)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
