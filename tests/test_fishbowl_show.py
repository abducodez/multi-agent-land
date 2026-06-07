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
        "step_slider",
        "back_btn",
        "play_btn",
        "fwd_btn",
        "speed",
        "timer",
        "poke_btns",
        "poke_text",
        "poke_send",
        "back_to_lab",
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
        "step_slider",
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


def test_step_slider_is_scrubber() -> None:
    handles = _build()
    slider = handles["step_slider"]
    assert isinstance(slider, gr.Slider)
    assert slider.value == 0
    assert slider.minimum == 0
    assert slider.step == 1


def test_speed_radio_options() -> None:
    handles = _build()
    speed = handles["speed"]
    assert isinstance(speed, gr.Radio)
    assert speed.value == "1×"
    assert [c[0] for c in speed.choices] == ["live", "1×", "fast"]


def test_transport_buttons_are_buttons() -> None:
    handles = _build()
    for key in ("back_btn", "play_btn", "fwd_btn", "back_to_lab", "poke_send"):
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
