"""Unit 7 — the Show tab component skeleton.

Mock-free: build the tree inside a real ``gr.Blocks`` and assert the returned
handles dict has the expected keys and Gradio component types.  No callbacks are
wired here (Unit 9 owns wiring); we only verify the tree shape and defaults.
"""

from __future__ import annotations

import gradio as gr

from src.ui.fishbowl.show import POKE_PRESETS, build_show


def _build() -> dict[str, object]:
    with gr.Blocks():
        return build_show()


def test_returns_expected_keys() -> None:
    handles = _build()
    expected = {
        "stage_html",
        "feed_html",
        "meters_html",
        "verdict_html",
        "layout",
        "mind_reader",
        "play_btn",
        "restart_btn",
        "timer",
        "poke_btns",
        "poke_text",
        "poke_send",
    }
    assert expected <= set(handles)


def test_recipe_subset() -> None:
    # Mirrors the e2e recipe's documented minimal assertion.
    handles = _build()
    assert {
        "stage_html",
        "feed_html",
        "meters_html",
        "layout",
        "mind_reader",
        "play_btn",
        "timer",
    } <= set(handles)


def test_html_panels_are_html() -> None:
    handles = _build()
    for key in ("stage_html", "feed_html", "meters_html", "verdict_html"):
        assert isinstance(handles[key], gr.HTML)


def test_layout_radio_defaults() -> None:
    handles = _build()
    layout = handles["layout"]
    assert isinstance(layout, gr.Radio)
    assert layout.value == "constellation"
    assert list(layout.choices) == [
        ("constellation", "constellation"),
        ("feed", "feed"),
        ("split", "split"),
    ]


def test_mind_reader_checkbox_defaults_sealed() -> None:
    handles = _build()
    mr = handles["mind_reader"]
    assert isinstance(mr, gr.Checkbox)
    assert mr.value is False


def test_pared_back_transport_controls_removed() -> None:
    # The Show is down to Play/Pause + Restart in the showbar: the step buttons,
    # scrubber, speed radio, manual judge, and back-to-Lab button were all removed
    # (tab nav covers Lab; the judge is now auto-summoned at the end of a run).
    handles = _build()
    for gone in ("step_slider", "back_btn", "fwd_btn", "speed", "judge_btn", "back_to_lab"):
        assert gone not in handles


def test_play_button_defaults_to_play_label() -> None:
    handles = _build()
    play = handles["play_btn"]
    assert isinstance(play, gr.Button)
    # Starts paused: the app shell flips it to "❚❚ Pause" on Summon (auto-start).
    assert play.value == "▶ Play"


def test_restart_button_present() -> None:
    handles = _build()
    restart = handles["restart_btn"]
    assert isinstance(restart, gr.Button)
    assert "Restart" in restart.value


def test_transport_buttons_are_buttons() -> None:
    handles = _build()
    for key in ("play_btn", "restart_btn", "poke_send"):
        assert isinstance(handles[key], gr.Button)


def test_timer_starts_inactive() -> None:
    handles = _build()
    timer = handles["timer"]
    assert isinstance(timer, gr.Timer)
    assert timer.active is False
    assert timer.value == 1.9


def test_poke_strip_shape() -> None:
    handles = _build()
    poke_btns = handles["poke_btns"]
    assert isinstance(poke_btns, list)
    assert len(poke_btns) == len(POKE_PRESETS)
    assert all(isinstance(b, gr.Button) for b in poke_btns)
    assert isinstance(handles["poke_text"], gr.Textbox)
