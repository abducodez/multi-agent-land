"""Pure HTML-string stage renderers for the Fishbowl Show.

Two of the three ways to watch the ledger live here:

  * ``render_constellation`` — the stage: a floating ``core`` glyph (scene + round
    counter) ringed by up to six MindCards, CSS-grid-placed at cardinal/diagonal slots
    (see ``ui/raw/show.css`` ``.constellation/.stage/.core/.ring``).
  * ``render_split`` — the Lab Readout: an omniscient table over the cast with
    name / said aloud / actually thinking columns.

These are *pure* functions of the ``view_model_at`` dict (the shipped contract): no
Gradio import, and no import of sibling render units.  ``render_constellation`` receives
the MindCard HTML already rendered by the app shell (keyed by agent id), so it stays
decoupled from the MindCard/avatar renderers.  All user text is ``html.escape``-d.
"""

from __future__ import annotations

import html

from src.ui.fishbowl.adapter import agent_color, agent_color_dim

# Fixed, evocative core glyph for the stage centre.  The prototype uses the scenario's
# own glyph; the view-model carries no glyph field, so we fall back to this (and honour
# a ``glyph`` key if a future view-model provides one).
_CORE_GLYPH = "◉"  # ◉


def _core(vm: dict) -> str:
    """The floating centre of the constellation: glyph, scene title, round counter."""
    glyph = html.escape(str(vm.get("glyph") or _CORE_GLYPH))
    scene = html.escape(str(vm.get("scene") or ""))
    rounds = vm.get("rounds") or 1
    max_rounds = vm.get("max_rounds")
    if max_rounds:
        round_text = f"Round {min(int(rounds), int(max_rounds))} / {int(max_rounds)}"
    else:
        round_text = f"Round {int(rounds)}"
    return (
        '<div class="core">'
        f'<div class="core-glyph disp">{glyph}</div>'
        f'<div class="core-title disp">{scene}</div>'
        f'<div class="core-round eyebrow">{html.escape(round_text)}</div>'
        f"{_secret_badge(vm)}"
        "</div>"
    )


def _secret_badge(vm: dict) -> str:
    """Audience-only reveal of a hidden-word run's secret (Twenty Sprouts).

    The view model carries ``secret`` only for hidden-word scenarios, and only the human
    watching the show ever sees it — it is never placed in any agent's prompt (see
    ``view_model.py``). Empty string for every other run, so the core is unchanged
    elsewhere. The 🔒 + "only you can see this" framing tells the viewer it's a peek
    behind the curtain, not something the cast knows.
    """
    secret = vm.get("secret")
    if not secret:
        return ""
    return (
        '<div class="core-secret eyebrow" title="The cast cannot see this — it never enters their prompts.">'
        "🔒 only you can see · the keeper holds "
        f'<b class="core-secret-word disp">{html.escape(str(secret))}</b>'
        "</div>"
    )


def render_constellation(vm: dict, cards_html_by_id: dict[str, str]) -> str:
    """Render the Constellation stage: a ``core`` ringed by pre-rendered MindCards.

    Iterates ``vm["cast"]`` in order and drops each ``cards_html_by_id[c["id"]]`` into a
    ``ring-slot`` (the CSS grid-areas position slots 1..6 at cardinal/diagonal points).
    A cast member with no rendered card simply yields an empty slot.
    """
    cast = vm.get("cast") or []
    slots = []
    for member in cast:
        card = cards_html_by_id.get(member.get("id"), "")
        slots.append(f'<div class="ring-slot">{card}</div>')
    ring = "".join(slots)
    return (
        '<div class="constellation">'
        '<div class="stage">'
        f"{_core(vm)}"
        f'<div class="ring" style="--n: {len(cast)}">{ring}</div>'
        "</div>"
        "</div>"
    )


def _split_cell(text: str | None, *, placeholder: str, think: bool) -> str:
    if text:
        cls = ' class="think-text"' if think else ""
        return f"<p{cls}>{html.escape(str(text))}</p>"
    return f'<p><span class="muted">{html.escape(placeholder)}</span></p>'


def render_split(vm: dict) -> str:
    """Render the Split view: an omniscient table of name / said aloud / actually thinking.

    The Lab Readout — every mind's public projection beside its private thought, in one
    table over ``vm["cast"]``.
    """
    cast = vm.get("cast") or []
    header = (
        '<div class="split-head">'
        '<span class="eyebrow">mind</span>'
        '<span class="eyebrow">said aloud — public projection</span>'
        '<span class="eyebrow">actually thinking — omniscient view</span>'
        "</div>"
    )
    rows = []
    for member in cast:
        name = html.escape(str(member.get("name") or member.get("id") or ""))
        archetype = html.escape(str(member.get("archetype") or ""))
        model = html.escape(str(member.get("model") or member.get("model_profile") or ""))
        speaking = " on" if member.get("speaking") else ""
        leak = " leak" if member.get("mood") == "panic" else ""
        said_cell = _split_cell(member.get("said"), placeholder="— hasn't spoken —", think=False)
        think_cell = _split_cell(member.get("thought"), placeholder="— quiet —", think=True)
        # Each row carries its mind's own phosphor so the omniscient table is colour-keyed
        # to the same hue the cast wears on stage and in the transcript.
        hue = member.get("hue")
        row_style = (
            f' style="--ac:{agent_color(int(hue))};--acd:{agent_color_dim(int(hue))}"' if hue is not None else ""
        )
        rows.append(
            f'<div class="split-row{speaking}"{row_style}>'
            '<div class="split-id">'
            f'<div class="disp split-name">{name}</div>'
            f'<div class="split-arch">{archetype}</div>'
            f'<div class="split-model">{model}</div>'
            "</div>"
            f'<div class="split-said disp">{said_cell}</div>'
            f'<div class="split-think{leak} disp">{think_cell}</div>'
            "</div>"
        )
    return (
        '<div class="constellation">'
        '<div class="stage">'
        '<div class="core panel">'
        f"{header}"
        f'<div class="ring splitview">{"".join(rows)}</div>'
        "</div>"
        "</div>"
        "</div>"
    )
