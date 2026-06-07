"""Avatar SVG renderer — an abstract circle face whose expression is mood-driven.

A pure HTML-string port of the prototype's ``Avatar`` component (``ui/raw/shared.jsx``):
mood selects the mouth path, eye state, and sweat drop; ``active`` adds a pulsing ring.
The colour is derived from the agent hue via the design's OKLch helpers.  No Gradio
import — the returned ``<svg>`` string is dropped straight into a ``gr.HTML`` block, and
Unit 1's CSS animates the emitted ``av-*`` classes.
"""

from __future__ import annotations

__all__ = ["render_avatar", "agent_color", "agent_color_dim"]


def agent_color(hue: int, lightness: float = 0.82, chroma: float = 0.14) -> str:
    """The agent's phosphor colour — all cast share L/C, only the hue varies."""
    return f"oklch({lightness} {chroma} {hue})"


def agent_color_dim(hue: int) -> str:
    """The dimmed companion colour (used for sealed / inactive surfaces)."""
    return f"oklch(0.5 0.09 {hue})"


# mouth path per mood (panic is drawn as an open 'o' instead of a path)
_MOUTHS: dict[str, str] = {
    "calm": "M37 62 Q50 70 63 62",
    "thinking": "M41 64 L59 64",
    "truth": "M35 60 Q50 75 65 60",
    "smug": "M40 66 Q50 64 60 59",
    "gossip": "M40 65 Q50 63 60 60",
    "lying": "M40 64 Q50 60 60 64",
    "panic": "",  # drawn as 'o'
}


def render_avatar(hue: int, mood: str = "calm", size: int = 64, active: bool = False) -> str:
    """Return an inline ``<svg>`` avatar string whose expression varies by mood.

    ``hue`` is an agent hue (0–360); ``mood`` selects the face; ``size`` is the pixel
    box; ``active`` draws the speaking ring.  Emits ``av-*`` classes so Unit 1's CSS can
    animate the avatar (ring pulse, blink, sweat, gasp, etc.).
    """
    col = agent_color(hue)
    dim = agent_color_dim(hue)
    sweating = mood in ("lying", "panic")
    big = mood == "panic"
    is_flat_eyes = mood in ("smug", "gossip")

    mouth_path = _MOUTHS.get(mood) or _MOUTHS["calm"]
    eye_ry = 6 if big else (1.6 if is_flat_eyes else 4)
    eye_rx = 5 if is_flat_eyes else 3.4

    ring = ""
    if active:
        ring = (
            f'<circle cx="50" cy="50" r="46" fill="none" stroke="{col}" '
            f'stroke-width="1.2" opacity="0.5" class="av-ring" />'
        )

    if is_flat_eyes:
        eyes = (
            f'<line x1="33" y1="45" x2="43" y2="45" stroke="{col}" stroke-width="2.4" stroke-linecap="round"/>'
            f'<line x1="57" y1="45" x2="67" y2="45" stroke="{col}" stroke-width="2.4" stroke-linecap="round"/>'
        )
    else:
        blink = " av-blink" if mood == "thinking" else ""
        eyes = (
            f'<ellipse cx="38" cy="45" rx="{eye_rx}" ry="{eye_ry}" fill="{col}" class="av-eye{blink}"/>'
            f'<ellipse cx="62" cy="45" rx="{eye_rx}" ry="{eye_ry}" fill="{col}" class="av-eye{blink}"/>'
        )

    if mood == "panic":
        mouth = f'<circle cx="50" cy="65" r="6" fill="none" stroke="{col}" stroke-width="2.4" class="av-gasp"/>'
    else:
        mouth = f'<path d="{mouth_path}" fill="none" stroke="{col}" stroke-width="2.4" stroke-linecap="round"/>'

    sweat = ""
    if sweating:
        sweat = (
            '<path d="M74 24 q5 8 0 13 q-5 -5 0 -13Z" fill="var(--blue)" '
            'class="av-sweat" style="filter: drop-shadow(0 0 4px var(--blue))"/>'
        )

    face = (
        f'<circle cx="50" cy="50" r="38" fill="color-mix(in oklab, {col} 12%, transparent)" '
        f'stroke="{col}" stroke-width="2.2" style="filter: drop-shadow(0 0 6px {col})" />'
    )

    # mood-specific animation class so Unit 1's CSS can target it (av-truth, av-smug, …)
    return (
        f'<div class="av av-{mood}" style="width:{size}px;height:{size}px;position:relative;--ac:{col};--acd:{dim}">'
        f'<svg viewBox="0 0 100 100" width="{size}" height="{size}" style="overflow:visible">'
        f"{ring}{face}{eyes}{mouth}{sweat}"
        f"</svg></div>"
    )
