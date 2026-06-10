"""Fishbowl feed renderer — pure HTML over a real ``view_model_at`` snapshot.

Zero mocks: we build a genuine ``vm`` from an engine ledger (narrate / say-with-thought /
poke / verdict) and assert the renderer emits the exact prototype CSS classes, honours
the mind-reader toggle, and slices the head narrate line for the typewriter effect.
"""

from __future__ import annotations

from src.core.events import Event
from src.core.registry import default_registry
from src.tools.builtins import default_tool_registry
from src.ui.fishbowl import view_model_at
from src.ui.fishbowl.render.feed import render_feed


def _ev(kind: str, actor: str, turn: int = 1, **payload) -> Event:
    return Event(run_id="r", turn=turn, kind=kind, actor=actor, payload=payload)


def _events() -> tuple[Event, ...]:
    return (
        _ev("run.started", "conductor", turn=0, seed="seed", goal="g"),
        # Genesis narration: emitted by the scenario itself → the anonymous narrator voice.
        _ev("world.observed", "thousand-token-wood", turn=0, text="the wood wakes & stirs"),
        # The seedkeeper is a cast member → credited to it as a say line, not the narrator.
        _ev("world.observed", "scene-whisperer", turn=1, text="the seedkeeper hums"),
        _ev("agent.spoke", "pocket-actor", turn=2, text="I want the moon", thought="secretly scared", mood="panic"),
        _ev("user.injected", "visitor", turn=3, text="a lantern hums", label="POKE"),
        _ev("judge.verdict", "mischief-critic", turn=4, text="keep it"),
    )


def _cast():
    scenario = default_registry().build_scenario("thousand-token-wood", tools=default_tool_registry())
    return [a.manifest for a in scenario.agents]


class TestRenderFeed:
    def test_emits_all_line_classes_and_voice(self):
        vm = view_model_at(_events(), 999, _cast(), scenario_name="thousand-token-wood")
        html = render_feed(vm, mind_reader=True)
        # container + every line class
        assert 'class="feed scroll"' in html
        assert "fe narr" in html
        assert "fe say" in html
        assert "fe poke" in html
        assert "verdict-fe" in html
        # narrator voice + said text + verdict + poke
        assert vm["voice_meta"]["name"] in html
        assert "I want the moon" in html
        assert "keep it" in html
        assert "a lantern hums" in html

    def test_mind_reader_gates_the_thought(self):
        vm = view_model_at(_events(), 999, _cast())
        on = render_feed(vm, mind_reader=True)
        off = render_feed(vm, mind_reader=False)
        assert "secretly scared" in on
        assert 'class="thought"' in on
        assert "secretly scared" not in off
        assert "thought" not in off

    def test_typewriter_slices_head_narrate(self):
        vm = view_model_at(_events(), 999, _cast())
        sliced = render_feed(vm, mind_reader=True, typed_n=3)
        # the narrate text "the wood wakes & stirs" → first 3 chars only
        assert "the wood wakes" not in sliced
        assert "the" in sliced
        assert "caret" in sliced  # still typing → caret shown
        # full text returns when typed_n is None
        full = render_feed(vm, mind_reader=True)
        assert "the wood wakes &amp; stirs" in full

    def test_html_is_escaped(self):
        vm = {
            "feed": [{"kind": "say", "agent": "<b>x</b>", "said": "1 < 2 & 3", "thought": None}],
            "voice_meta": {"name": "NARRATOR"},
        }
        html = render_feed(vm, mind_reader=True)
        assert "<b>x</b>" not in html
        assert "&lt;b&gt;x&lt;/b&gt;" in html
        assert "1 &lt; 2 &amp; 3" in html

    def test_dense_modifier(self):
        vm = {"feed": [], "voice_meta": {"name": "NARRATOR"}}
        assert 'class="feed scroll dense"' in render_feed(vm, mind_reader=False, dense=True)
        assert 'class="feed scroll"' in render_feed(vm, mind_reader=False, dense=False)
