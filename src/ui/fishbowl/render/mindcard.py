"""MindCard renderer — the say-vs-think flip card (the whole point of the Show).

A pure HTML-string port of the prototype's ``MindCard`` component (``ui/raw/show.jsx``):
a flippable card whose front shows the avatar, name, archetype, tier dot, mood label,
and two bubbles ("said aloud" / "actually thinking").  When the mind-reader is off the
thought bubble is sealed; when the agent is panicking the secret "leaks"; on a verdict
the card flips to a back face revealing the truth.  No Gradio import — the returned
string drops into a ``gr.HTML`` block, and Unit 1's CSS animates the emitted classes.
"""

from __future__ import annotations

import html

from src.ui.fishbowl.adapter import TIER_COLOR
from src.ui.fishbowl.render.avatar import agent_color, agent_color_dim, render_avatar

__all__ = ["render_mindcard"]

# small "eye off" glyph for the sealed-thought placeholder (monoline, matches the proto)
_EYE_OFF = (
    '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">'
    '<path d="M2 12s4-7 10-7c2 0 3.8.7 5.3 1.6M22 12s-4 7-10 7c-2 0-3.8-.7-5.3-1.6" '
    'fill="none" stroke="currentColor" stroke-width="1.6"/>'
    '<path d="M4 4l16 16" stroke="currentColor" stroke-width="1.6"/></svg>'
)

_MUTED = '<span class="muted">{text}</span>'


def _avatar_size(variant: str) -> int:
    """The prototype's per-variant avatar size (row=40, stage/split=50)."""
    return 40 if variant == "row" else 50


def render_mindcard(
    card: dict,
    *,
    mind_reader: bool,
    flipped: bool = False,
    variant: str = "ring",
    secret: str | None = None,
) -> str:
    """Render one cast card (a ``view_model_at(...)["cast"][i]`` element) as a flip card.

    ``mind_reader`` gates the thought bubble (sealed placeholder when off).  ``flipped``
    mounts the verdict back face.  ``variant`` selects the card flavour (``ring`` /
    ``stage`` / ``row`` / ``split``) used in the CSS class.  ``secret`` overrides the
    back-face reveal text; by default the back is built from the role.
    """
    hue = int(card.get("hue", 190))
    col = agent_color(hue)
    dim = agent_color_dim(hue)
    mood = card.get("mood", "calm")
    speaking = bool(card.get("speaking"))
    spoke = bool(card.get("spoke"))

    classes = ["mind", f"mind-{variant}"]
    if speaking:
        classes.append("speaking")
    if mood == "panic":
        classes.append("rattled")
    if flipped:
        classes.append("flipped")
    cls = " ".join(classes)

    # the avatar wears the live mood while speaking or once it has spoken, else calm
    face_mood = mood if (speaking or spoke) else "calm"
    avatar = render_avatar(hue, face_mood, size=_avatar_size(variant), active=speaking)

    name = html.escape(str(card.get("name", "")))
    archetype = html.escape(str(card.get("archetype", "")))
    # The real model the cast member is running (ADR-0022 endpoint override), falling back
    # to the profile tier name when it routes purely by profile.
    model = html.escape(str(card.get("model") or card.get("model_profile", "")))
    mood_label = html.escape(str(card.get("mood_label", "")))
    tier = card.get("tier", "mid")
    tier_color = TIER_COLOR.get(tier, "var(--cyan)")

    mic = '<span class="mic">●</span>' if speaking else ""

    # ── said-aloud bubble ────────────────────────────────────────────────────
    said = card.get("said")
    said_body = html.escape(str(said)) if said else _MUTED.format(text="— hasn't spoken —")

    # ── thought bubble (sealed when the mind-reader is off) ──────────────────
    leak = " leak" if mood == "panic" else ""
    if mind_reader:
        thought = card.get("thought")
        thought_body = html.escape(str(thought)) if thought else _MUTED.format(text="— quiet in here —")
        thought_tag = "actually thinking"
        thought_inner = f'<p class="think-text">{thought_body}</p>'
        thought_state = " open"
    else:
        thought_tag = "mind sealed"
        thought_inner = f'<div class="seal">{_EYE_OFF} flip “read their minds” to look inside</div>'
        thought_state = " sealed"

    front = (
        '<div class="mind-face mind-front">'
        '<div class="mind-head">'
        f"{avatar}"
        '<div class="mind-id">'
        f'<div class="mind-name disp">{name}{mic}</div>'
        f'<div class="mind-arch">{archetype}</div>'
        "</div>"
        '<div class="mind-meta">'
        f'<span class="mind-model" title="model"><span class="tier-dot" style="background:{tier_color}"></span>{model}</span>'
        f'<span class="mind-mood">{mood_label}</span>'
        "</div>"
        "</div>"
        '<div class="bubbles">'
        '<div class="bubble said">'
        '<span class="bub-tag">said aloud</span>'
        f"<p>{said_body}</p>"
        "</div>"
        f'<div class="bubble thought{thought_state}{leak}">'
        f'<span class="bub-tag">{thought_tag}</span>'
        f"{thought_inner}"
        "</div>"
        "</div>"
        "</div>"
    )

    # ── back face (verdict reveal) — only mounted when flipped, so it never leaks ─
    back = ""
    if flipped:
        if secret is not None:
            reveal_text = html.escape(str(secret))
        else:
            role = html.escape(str(card.get("role", "")))
            reveal_text = role or html.escape(str(card.get("said") or ""))
        back = (
            '<div class="mind-face mind-back">'
            '<div class="reveal-glyph disp">✶</div>'
            '<div class="eyebrow">the truth</div>'
            f'<div class="reveal-secret disp">{reveal_text}</div>'
            "</div>"
        )

    return f'<div class="{cls}" style="--ac:{col};--acd:{dim}"><div class="mind-inner">{front}{back}</div></div>'
