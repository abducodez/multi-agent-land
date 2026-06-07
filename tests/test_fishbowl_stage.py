"""Mock-free tests for the Fishbowl stage renderers (constellation + split).

Builds a real ``view_model_at`` snapshot over a small ledger of engine events, then
exercises the pure HTML renderers in ``src.ui.fishbowl.render.stage``.
"""

from __future__ import annotations

import html

from src.core.events import Event
from src.core.registry import default_registry
from src.tools.builtins import default_tool_registry
from src.ui.fishbowl import view_model_at
from src.ui.fishbowl.render.stage import render_constellation, render_split


def _ev(kind: str, actor: str, turn: int = 1, **payload) -> Event:
    return Event(run_id="r", turn=turn, kind=kind, actor=actor, payload=payload)


def _events() -> tuple[Event, ...]:
    return (
        _ev("run.started", "conductor", turn=0, seed="seed", goal="g"),
        _ev("world.observed", "scene-whisperer", turn=1, text="the wood wakes"),
        _ev("agent.spoke", "pocket-actor", turn=2, text="I want the moon", thought="scared", mood="panic"),
        _ev("user.injected", "visitor", turn=3, text="a lantern hums", label="POKE"),
        _ev("judge.verdict", "mischief-critic", turn=4, text="keep it", mood="smug"),
    )


def _cast():
    scenario = default_registry().build_scenario("thousand-token-wood", tools=default_tool_registry())
    return [a.manifest for a in scenario.agents]


def _vm() -> dict:
    events, cast = _events(), _cast()
    return view_model_at(events, len(events), cast, scenario_name="thousand-token-wood")


class TestRenderConstellation:
    def test_structure_and_core(self):
        vm = _vm()
        cards = {c["id"]: f"<div class='mind'>card-{c['id']}</div>" for c in vm["cast"]}
        out = render_constellation(vm, cards)
        assert "constellation" in out
        assert "core" in out
        assert "ring" in out
        assert "ring-slot" in out
        # scene title and round counter land in the core
        assert html.escape(vm["scene"]) in out
        assert "Round" in out

    def test_each_card_is_injected_in_cast_order(self):
        vm = _vm()
        cards = {c["id"]: f"<div class='mind'>card-{c['id']}</div>" for c in vm["cast"]}
        out = render_constellation(vm, cards)
        positions = []
        for c in vm["cast"]:
            marker = f"card-{c['id']}"
            assert marker in out
            positions.append(out.index(marker))
        assert positions == sorted(positions)  # rendered in cast order
        assert out.count("ring-slot") == len(vm["cast"])

    def test_missing_card_yields_empty_slot(self):
        vm = _vm()
        out = render_constellation(vm, {})  # no cards supplied
        assert out.count("ring-slot") == len(vm["cast"])


class TestRenderSplit:
    def test_header_and_a_row_per_cast_member(self):
        vm = _vm()
        out = render_split(vm)
        assert "constellation" in out
        assert "split-head" in out
        assert out.count("split-row") == len(vm["cast"])
        for c in vm["cast"]:
            assert html.escape(c["name"]) in out

    def test_said_and_thought_render(self):
        vm = _vm()
        out = render_split(vm)
        pa = next(c for c in vm["cast"] if c["id"] == "pocket-actor")
        assert html.escape(pa["said"]) in out  # "I want the moon"
        assert html.escape(pa["thought"]) in out  # "scared"
        assert "split-said" in out and "split-think" in out
        # the panicking mind leaks
        assert "leak" in out

    def test_html_is_escaped(self):
        vm = _vm()
        vm["cast"][0]["said"] = "<script>alert(1)</script>"
        out = render_split(vm)
        assert "<script>alert(1)</script>" not in out
        assert "&lt;script&gt;" in out
