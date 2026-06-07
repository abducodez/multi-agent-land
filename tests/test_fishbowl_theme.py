"""Mock-free tests for the Fishbowl theme + CSS/head assets (Unit 1).

These assert the styling surface is wired correctly: the stylesheet carries the
class names and custom properties the renderers rely on, the head markup loads
both fonts, and the theme is import-/construct-safe without launching Gradio.
"""

from __future__ import annotations

from src.ui.fishbowl.theme import FISHBOWL_HEAD, FishbowlTheme, load_css


def test_load_css_returns_nonempty_string() -> None:
    css = load_css()
    assert isinstance(css, str)
    assert len(css) > 1000  # the full ported stylesheet, not a stub


def test_css_contains_key_classes() -> None:
    css = load_css()
    # mind card + flip states
    for token in ("mind", "mind-inner", "mind-face", "flipped", "speaking", "rattled", "mic"):
        assert token in css, f"missing mind class: {token}"
    # CRT atmosphere layers
    for token in ("crt-bg", "crt-grid", "crt-scan", "crt-vignette", "crt-flicker"):
        assert token in css, f"missing CRT layer: {token}"
    # feed kinds
    for token in ("narr", "say", "poke", "verdict-fe"):
        assert token in css, f"missing feed class: {token}"
    # stage / constellation (scoped under the .fishbowl root)
    assert ".constellation" in css
    assert ".fishbowl .stage" in css
    assert ".fishbowl .core" in css
    assert ".fishbowl .ring" in css
    # transport + meters
    for token in ("transport", "scrub", "seg", "seg-b", "meters", "stat"):
        assert token in css, f"missing chrome class: {token}"


def test_css_contains_avatar_animations() -> None:
    css = load_css()
    for token in (
        "av-ring",
        "av-blink",
        "av-sweat",
        "av-gasp",
        "av-thinking",
        "av-panic",
        "av-smug",
        "av-gossip",
        "av-truth",
        "av-calm",
    ):
        assert token in css, f"missing avatar anim class: {token}"


def test_css_contains_color_variables() -> None:
    css = load_css()
    for var in (
        "--bg-0",
        "--bg-1",
        "--bg-2",
        "--panel",
        "--ink",
        "--ink-mid",
        "--ink-dim",
        "--ink-faint",
        "--cyan",
        "--teal",
        "--blue",
        "--violet",
        "--amber",
        "--coral",
        "--lime",
        "--r",
        "--r-lg",
        "--glow",
    ):
        assert var in css, f"missing CSS var: {var}"


def test_css_scopes_under_fishbowl_and_hides_footer() -> None:
    css = load_css()
    assert ".fishbowl" in css
    # default Gradio footer must be hidden
    assert "footer" in css
    assert "display: none" in css


def test_head_references_both_fonts() -> None:
    assert isinstance(FISHBOWL_HEAD, str)
    assert "Martian+Mono" in FISHBOWL_HEAD or "Martian Mono" in FISHBOWL_HEAD
    assert "IBM+Plex+Mono" in FISHBOWL_HEAD or "IBM Plex Mono" in FISHBOWL_HEAD
    assert "fonts.googleapis.com" in FISHBOWL_HEAD
    assert "preconnect" in FISHBOWL_HEAD


def test_theme_constructs_without_launch() -> None:
    theme = FishbowlTheme()
    # font set to the body display fonts requested
    assert theme is not None
    # the theme is a gradio Base subclass
    import gradio as gr

    assert isinstance(theme, gr.themes.Base)
