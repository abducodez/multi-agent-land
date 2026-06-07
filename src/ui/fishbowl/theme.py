"""Fishbowl theme + CSS/head assets for the Gradio shell.

This module is the styling surface of the Fishbowl UI.  It is import-safe —
constructing :class:`FishbowlTheme` or reading the assets never launches Gradio
or touches the engine.  The app shell (Unit 9) wires it up with::

    from src.ui.fishbowl.theme import FISHBOWL_HEAD, FishbowlTheme, load_css

    demo.launch(css=load_css(), head=FISHBOWL_HEAD, theme=FishbowlTheme())

The phosphor / aquatic-CRT look is ported from the ``ui/raw`` prototype:

  * ``assets/styles.css`` — the full stylesheet, scoped under a ``.fishbowl``
    root so it survives Gradio's own cascade, with the CRT overlay layers left
    full-bleed.
  * ``assets/head.html`` — the Google Fonts links (Martian Mono + IBM Plex Mono).

:class:`FishbowlTheme` maps the same dark phosphor palette onto Gradio's theme
variables so the built-in widgets (sliders, dropdowns, buttons rendered by
Gradio itself) sit comfortably inside the theater.
"""

from __future__ import annotations

from pathlib import Path

import gradio as gr

__all__ = ["FISHBOWL_HEAD", "FishbowlTheme", "load_css"]

_ASSETS = Path(__file__).parent / "assets"

# ---- palette (mirrors the CSS custom properties in assets/styles.css) ----
_BG_0 = "#05121a"  # deepest tank
_BG_1 = "#07181f"
_BG_2 = "#0a222b"
_PANEL_SOLID = "#0c2730"
_INK = "#c4f6ee"
_INK_MID = "#79c3bb"
_INK_DIM = "#4d8983"
_CYAN = "#4fe6d2"
_TEAL = "#2bc4b4"


def load_css() -> str:
    """Return the Fishbowl stylesheet as a string for ``gr.Blocks(css=...)``.

    Reads ``assets/styles.css`` relative to this file, so it works regardless
    of the process working directory.
    """
    return (_ASSETS / "styles.css").read_text(encoding="utf-8")


def _load_head() -> str:
    """Read ``assets/head.html`` for ``demo.launch(head=...)``."""
    return (_ASSETS / "head.html").read_text(encoding="utf-8")


#: The ``<head>`` markup (font links + meta) passed to ``demo.launch(head=...)``.
FISHBOWL_HEAD: str = _load_head()


class FishbowlTheme(gr.themes.Base):
    """A dark phosphor / aquatic-CRT Gradio theme for the Fishbowl app.

    Subclasses :class:`gr.themes.Base` and overrides the colour, font, and
    radius variables so Gradio's own widgets blend into the theater.  The
    bespoke theater markup is styled by :func:`load_css`; this theme covers the
    surrounding Gradio chrome.
    """

    def __init__(self) -> None:
        super().__init__(
            primary_hue=gr.themes.colors.teal,
            secondary_hue=gr.themes.colors.cyan,
            neutral_hue=gr.themes.colors.slate,
            font=gr.themes.GoogleFont("IBM Plex Mono"),
            font_mono=gr.themes.GoogleFont("Martian Mono"),
        )
        # Map the aquatic phosphor palette onto Gradio's theme variables so the
        # built-in components match the ported .fishbowl markup.
        super().set(
            # backgrounds
            body_background_fill=_BG_0,
            body_background_fill_dark=_BG_0,
            background_fill_primary=_BG_1,
            background_fill_primary_dark=_BG_1,
            background_fill_secondary=_BG_2,
            background_fill_secondary_dark=_BG_2,
            block_background_fill=_PANEL_SOLID,
            block_background_fill_dark=_PANEL_SOLID,
            panel_background_fill=_PANEL_SOLID,
            panel_background_fill_dark=_PANEL_SOLID,
            # text
            body_text_color=_INK,
            body_text_color_dark=_INK,
            body_text_color_subdued=_INK_MID,
            body_text_color_subdued_dark=_INK_MID,
            block_label_text_color=_INK_DIM,
            block_label_text_color_dark=_INK_DIM,
            block_title_text_color=_INK_MID,
            block_title_text_color_dark=_INK_MID,
            # borders
            border_color_primary="*neutral_700",
            block_border_color="*neutral_700",
            # accents — cyan/teal phosphor
            color_accent=_CYAN,
            color_accent_soft=_TEAL,
            button_primary_background_fill=_TEAL,
            button_primary_background_fill_dark=_TEAL,
            button_primary_text_color=_BG_0,
            button_primary_text_color_dark=_BG_0,
            slider_color=_CYAN,
            slider_color_dark=_CYAN,
            # radii to match --r / --r-lg
            block_radius="8px",
            button_large_radius="4px",
            button_small_radius="4px",
            input_radius="4px",
        )
