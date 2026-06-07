"""Meters + verdict renderers — pure HTML strings for the Fishbowl theater.

These mirror the prototype's ``Meters``/``Stat`` components and the verdict banner
(``ui/raw/show.jsx`` + ``ui/raw/show.css``), but bind to *real* run data via the shipped
``view_model_at`` snapshot: the token meter prefers ``tokens_real`` (the governor's
``total_tokens``) over the scrubber estimate, and the budget bar / status pill reflect the
true ``token_ceiling`` (G9).  No Gradio import, no sibling render imports — the same string
feeds ``gr.HTML`` now and a future ``gr.Server`` JSON endpoint.
"""

from __future__ import annotations

import html

# The bar turns from cyan to coral once usage crosses this fraction of the ceiling.
_WARN_FRACTION = 0.85


def _tokens_used(vm: dict) -> int:
    """Prefer the governor's real ``total_tokens``; fall back to the scrubber estimate."""
    real = vm.get("tokens_real")
    if isinstance(real, dict) and real.get("total_tokens") is not None:
        return int(real["total_tokens"])
    return int(vm.get("tokens") or 0)


def render_meters(vm: dict) -> str:
    """A ``meters`` panel: a token-budget bar (when a ceiling is set) plus stat pills.

    Pills: tokens used (with ceiling when known), the round, and a status pill that flips
    to ``BUDGET OUT`` (coral) once the ceiling is reached.
    """
    used = _tokens_used(vm)
    ceiling = vm.get("token_ceiling")

    pct: int | None = None
    if ceiling:
        pct = min(100, round(used / ceiling * 100))
    warn = pct is not None and pct >= round(_WARN_FRACTION * 100)
    out_of_budget = pct is not None and pct >= 100
    accent = "var(--coral)" if warn else "var(--cyan)"

    # Token value: "1,234 / 5,000" when a ceiling is known, else just "1,234".
    tokens_value = f"{used:,} / {ceiling:,}" if ceiling else f"{used:,}"

    bar = ""
    if pct is not None:
        bar = (
            '<div class="bar">'
            f'<div class="bar-fill" style="width:{pct}%;background:{accent};color:{accent}"></div>'
            "</div>"
        )

    # Rounds pill: "n/max" when a max is known, else just "n".
    rounds = int(vm.get("rounds") or 0)
    max_rounds = vm.get("max_rounds")
    rounds_value = f"{min(rounds, int(max_rounds))}/{int(max_rounds)}" if max_rounds else str(rounds)

    status_label = "BUDGET OUT" if out_of_budget else "RUNNING"
    status_color = "var(--coral)" if out_of_budget else "var(--cyan)"

    return (
        '<div class="meters panel">'
        '<div class="meter">'
        '<div class="meter-h">'
        '<span class="eyebrow">Token budget</span>'
        f'<span class="tnum" style="color:{accent}">{html.escape(tokens_value)}</span>'
        "</div>"
        f"{bar}"
        "</div>"
        '<div class="meter-stats">'
        f"{_stat('Tokens', tokens_value, accent)}"
        f"{_stat('Round', rounds_value, 'var(--cyan)')}"
        f"{_stat('Status', status_label, status_color)}"
        "</div>"
        "</div>"
    )


def _stat(label: str, value: str, color: str | None = None) -> str:
    """A single ``stat`` pill: an eyebrow label over a display-font value."""
    style = f' style="color:{color}"' if color else ""
    return (
        '<div class="stat">'
        f'<span class="eyebrow">{html.escape(label)}</span>'
        f'<span class="disp tnum"{style}>{html.escape(value)}</span>'
        "</div>"
    )


def render_verdict(vm: dict) -> str:
    """The verdict banner — empty string until the Judge has ruled.

    Shows the verdict text and, when present, one reveal line per ``{agent, secret, role}``
    so the truth lands at the end of the show.
    """
    verdict = vm.get("verdict")
    if not verdict:
        return ""

    text = html.escape(str(verdict.get("text", "")))
    reveal = verdict.get("reveal") or []

    lines = ""
    for r in reveal:
        agent = html.escape(str(r.get("agent", "")))
        secret = html.escape(str(r.get("secret", "")))
        role = html.escape(str(r.get("role", "")))
        lines += (
            '<div class="reveal-line">'
            f'<span class="disp reveal-agent">{agent}</span>'
            f'<span class="reveal-secret">{secret}</span>'
            f'<span class="eyebrow reveal-role">{role}</span>'
            "</div>"
        )

    return (
        '<div class="verdict banner">'
        '<div class="eyebrow">&#9878; Verdict</div>'
        f'<div class="disp vb-text">{text}</div>'
        f"{lines}"
        "</div>"
    )
