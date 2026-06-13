"""The winner celebration — a one-shot, cheerful "champion" moment for the Show.

This is the *modular* half of the verdict surface (ADR-0029).  The verdict banner
(``render.meters.render_verdict``) is the sober ruling; this is the confetti.  It is a
pure HTML string, gated on one thing: did the run declare a winner?  A ``none``-kind
scenario (Wood, Oracle) never sets ``verdict.winner_label``, so ``render_winner`` returns
``""`` and adds nothing to the page — the celebration is *opt-in by data*, not bolted onto
every scenario.

The card names the winning **bot**: for an agent win it shows the mind's archetype and the
model that actually ran; for a team win it lines up the roster as chips and names the side
it dethroned.  It tints itself with the winner's own hue (``hsl(var(--win-hue) …)``) so the
champion of a teal mind glows teal.  No Gradio import — the same string feeds ``gr.HTML``
today and a future JSON endpoint.
"""

from __future__ import annotations

import html

# The phosphor palette, in confetti form — cycles through the theme accents so the burst
# reads as "our CRT theater is celebrating", not a generic party popper.
_CONFETTI_COLORS = ("var(--lime)", "var(--cyan)", "var(--amber)", "var(--violet)", "var(--coral)")
_CONFETTI_COUNT = 30


def _pretty(slug: str) -> str:
    """``spy-nil`` → ``Spy Nil`` — the same shape ``_winner_label`` uses for the ribbon."""
    return slug.replace("-", " ").replace("_", " ").title()


def _confetti() -> str:
    """A finite burst of phosphor confetti.

    Each bit falls exactly once (``animation-iteration-count: 1`` + ``forwards`` in the CSS),
    so the sky clears after a few seconds and the champion card is left glowing on its own —
    cheerful, not a perpetual distraction.  Geometry is index-derived (deterministic) so the
    burst is stable across re-renders and trivial to snapshot in tests.
    """
    bits: list[str] = []
    for i in range(_CONFETTI_COUNT):
        color = _CONFETTI_COLORS[i % len(_CONFETTI_COLORS)]
        left = (i * 37) % 100  # spread across the width without an even, mechanical comb
        delay = round((i % 10) * 0.13, 2)  # stagger the fall so it rains, not drops as a sheet
        dur = round(2.6 + (i % 6) * 0.32, 2)
        drift = ((i * 53) % 140) - 70  # -70…70px lateral sway as it falls
        rot = (i * 47) % 360
        klass = "wf-bit round" if i % 3 == 0 else "wf-bit"
        style = (
            f"left:{left}%;background:{color};"
            f"animation-delay:{delay}s;animation-duration:{dur}s;"
            f"--wf-drift:{drift}px;--wf-rot:{rot}deg"
        )
        bits.append(f'<i class="{klass}" style="{style}"></i>')
    return f'<div class="wf-confetti" aria-hidden="true">{"".join(bits)}</div>'


def _chip(text: str) -> str:
    return f'<span class="wf-chip">{html.escape(text)}</span>'


def render_winner(vm: dict) -> str:
    """The champion celebration — ``""`` until (and unless) the run declares a winner.

    Reads ``vm["verdict"]`` (the same dict the banner uses) plus ``vm["cast"]`` /
    ``vm["teams"]`` to name the winning bot.  Renders nothing when ``winner_label`` is
    absent, which is every ``none``-kind scenario and any legacy verdict — that absence is
    the modularity contract, so this can be composed into every Show without touching the
    scenarios that have no winner to crown.
    """
    verdict = vm.get("verdict") or {}
    label = verdict.get("winner_label")
    if not label:
        return ""

    winner = verdict.get("winner")
    kind = verdict.get("winner_kind")
    correct = verdict.get("correct")
    cast = vm.get("cast") or []
    teams = vm.get("teams") or {}
    by_id = {c.get("id"): c for c in cast}

    # Default phosphor-lime hue, overridden by the winning bot's own hue so the card's glow
    # matches the mind on the stage that just won.
    hue = 95
    sub = ""
    roster = ""

    if kind == "team":
        members = [by_id[m] for m in (teams.get(winner) or []) if m in by_id]
        if members:
            hue = int(members[0].get("hue", hue))
            chips = "".join(_chip(str(m.get("name", ""))) for m in members)
            roster = f'<div class="wf-roster">{chips}</div>'
    else:  # an individual mind took it
        card = by_id.get(winner)
        if card:
            hue = int(card.get("hue", hue))
            arch = str(card.get("archetype", "")).strip()
            model = str(card.get("model", "")).strip()
            parts = ""
            if arch:
                parts += f'<span class="wf-arch">{html.escape(arch)}</span>'
            if model:
                parts += f'<span class="wf-chip wf-model">{html.escape(model)}</span>'
            sub = f'<div class="wf-sub">{parts}</div>' if parts else ""

    # The losing side(s) — named only for team contests, where the roster makes "who lost"
    # unambiguous. An agent/judged win has no single named loser, so we let it stand alone.
    losers = [t for t in teams if t != winner] if kind == "team" else []
    loser_line = (
        f'<div class="wf-loser">outplayed {html.escape(", ".join("Team " + _pretty(t) for t in losers))}</div>'
        if losers
        else ""
    )

    # Ground-truth miss (the spy slipped past the herd): still a win for the spy, so we
    # celebrate it — just with a wink instead of a trophy.
    if correct is False:
        glyph, eyebrow, cheer = "&#129399;", "Clean Getaway", "Slipped away without a trace."
    elif kind == "team":
        glyph, eyebrow, cheer = "&#127942;", "Champions", "They read the room — and won it."
    else:
        glyph, eyebrow, cheer = "&#127942;", "Champion", "The sharpest mind in the bowl."

    # Dismissable with no JS (Gradio strips scripts/handlers from gr.HTML): a hidden
    # checkbox is the toggle, and the backdrop + the ✕ button are <label>s that flip it.
    # ``:checked`` then hides the whole overlay, which frees the pointer back to the
    # transport. Re-rendering (e.g. a scrub) resets the checkbox, so the moment can replay.
    return (
        f'<div class="winner-fx" role="status" aria-live="polite" style="--win-hue:{hue}">'
        # autocomplete=off so the browser never restores a stale "checked" (dismissed)
        # state onto a freshly-rendered celebration — every new winner shows by default.
        '<input type="checkbox" id="wf-dismiss" class="wf-dismiss" autocomplete="off" aria-hidden="true" tabindex="-1">'
        '<label class="wf-backdrop" for="wf-dismiss" title="Dismiss"></label>'
        f"{_confetti()}"
        '<div class="wf-card">'
        '<label class="wf-x" for="wf-dismiss" role="button" aria-label="Close celebration" title="Close">&#215;</label>'
        '<div class="wf-rays" aria-hidden="true"></div>'
        f'<div class="wf-trophy" aria-hidden="true">{glyph}</div>'
        f'<div class="wf-eyebrow">{glyph} {eyebrow}</div>'
        f'<div class="wf-name disp">{html.escape(str(label))}</div>'
        f"{sub}"
        f"{roster}"
        f'<div class="wf-cheer">{cheer}</div>'
        f"{loser_line}"
        "</div>"
        "</div>"
    )
