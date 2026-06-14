"""HALL OF FAME — the phosphor-arcade high-score board (Workstream 6.2 + 6.3).

A cheerful, screenshot-worthy scoreboard for the competitive results the engine has
recorded.  It is a *pure read surface* over the dedicated leaderboard table (ADR-0035):
every number on the board comes from the aggregations in :mod:`src.core.leaderboard`
(``headline`` / ``model_table`` / ``scenario_sessions`` / ``fairness_table``), which fold
the :class:`~src.core.leaderboard_store.LeaderboardEntry` rows persisted in
``leaderboard_entries`` — a table *detached from the event ledger* (the ``events`` log
stays the trace; this is the materialised scoreboard).

Design — "Phosphor Arcade High-Score Board"
-------------------------------------------
The tab lives inside the existing ``.fishbowl`` CRT-phosphor scope and reuses the
theme's design tokens, fonts (Martian Mono display, IBM Plex Mono body) and existing
classes (``eyebrow`` / ``chip`` / ``panel`` / verdict + winner chrome).  Champions glow
**gold/amber** against the cool teal CRT — that warm/cool contrast is the whole
identity.  Only two scoped vars are introduced (``--gold`` / ``--gold-soft``) plus the
Hall-of-Fame layout classes, all under ``.fishbowl.fb-hall``.

Everything here is **offline-safe**: pure HTML strings, an inlined ``<style>`` block,
no network calls and no external assets.  When the store is empty or unconfigured (the
deterministic stub path before any competitive run finishes) every pane degrades to a
cheerful empty state rather than crashing.

The per-row **Replay** button mirrors the Lab's Archive drawer exactly: it turns a run
into a read-only :class:`~src.ui.fishbowl.session.ReplaySession` via
:func:`src.ui.fishbowl.archive.load_replay` and pushes it into the Show transport
through the same ``gr.render`` Load-button contract (see ``build_hall_of_fame``).
"""

from __future__ import annotations

import html as _html
from datetime import datetime

import gradio as gr

from src.core.leaderboard import (
    ModelRow,
    SeatRow,
    fairness_table,
    headline,
    model_table,
    scenario_sessions,
)
from src.core.leaderboard_store import LeaderboardEntry
from src.core.registry import default_registry


# ── data access (defensive: empty store / no backend → empty projections) ────────


def _entries() -> list[LeaderboardEntry]:
    """Every scoreboard row from the dedicated leaderboard table, or ``[]`` if unavailable.

    Reads the ``leaderboard_entries`` table (ADR-0035) — *detached from the event ledger*
    — via :func:`make_leaderboard_store`.  Defensive on every axis: a missing/unconfigured
    store or any read error degrades to an empty list so the board shows its cheerful empty
    state instead of raising.
    """
    try:
        from src.core.leaderboard_store import make_leaderboard_store

        return list(make_leaderboard_store().entries())
    except Exception:  # pragma: no cover - no store configured / read error (defensive)
        return []


def competitive_scenarios() -> list[tuple[str, str]]:
    """``(title, internal_name)`` pairs for scenarios whose ``competition.kind != none``.

    These are the only worlds that can ever crown a champion, so the scenario picker
    is scoped to them.  Returns ``[]`` when the registry can't be read.
    """
    try:
        registry = default_registry()
    except Exception:  # pragma: no cover - registry unavailable (defensive)
        return []
    out: list[tuple[str, str]] = []
    for name in sorted(registry.scenarios):
        cfg = registry.scenarios[name]
        comp = getattr(cfg, "competition", None)
        if getattr(comp, "kind", "none") not in ("none", None):
            out.append(((getattr(cfg, "title", "") or name), name))
    return out


# ── tiny formatting helpers ──────────────────────────────────────────────────────


def _esc(text: object) -> str:
    """HTML-escape any value (defensive — inputs may be ``None`` / non-str)."""
    return _html.escape(str(text if text is not None else ""))


def _short_model(endpoint: str) -> str:
    """Compact a model endpoint to its last path segment (``a/b/Model`` → ``Model``)."""
    if not endpoint:
        return "—"
    return endpoint.rsplit("/", 1)[-1] or endpoint


def _pct(rate: float) -> str:
    """A win-rate float as a whole-percent label (``0.7`` → ``"70%"``)."""
    try:
        return f"{round(rate * 100)}%"
    except Exception:  # pragma: no cover - non-numeric (defensive)
        return "0%"


def _bar_width(rate: float) -> float:
    """Clamp a win-rate to a 0–100 bar width."""
    try:
        return max(0.0, min(100.0, float(rate) * 100.0))
    except Exception:  # pragma: no cover - non-numeric (defensive)
        return 0.0


def _short_id(run_id: str) -> str:
    """A glanceable run handle — last 4 of its uuid (matches the Archive)."""
    tail = run_id.replace("-", "")[-4:] if run_id else "????"
    return f"#{tail}"


def _fmt_when(value: datetime | None) -> str:
    """A compact ``Jun 12 · 14:03`` timestamp, ``—`` when missing."""
    if not isinstance(value, datetime):
        return "—"
    try:
        return value.strftime("%b %-d · %H:%M")
    except ValueError:  # pragma: no cover - platforms without %-d
        return value.strftime("%b %d · %H:%M")


def _fmt_tokens(tokens: int) -> str:
    try:
        tokens = int(tokens)
    except Exception:  # pragma: no cover - non-numeric (defensive)
        return "0"
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}k"
    return str(tokens)


_REASON_LABEL = {
    "verdict": "verdict",
    "budget": "budget",
    "tick_cap": "tick-cap",
    "user_stop": "stopped",
}


def _cast_models(row: LeaderboardEntry) -> str:
    """A compact roster of the distinct models that played a session."""
    seen: list[str] = []
    for binding in (row.cast or {}).values():
        endpoint = getattr(binding, "model_endpoint", None)
        if endpoint:
            short = _short_model(endpoint)
            if short not in seen:
                seen.append(short)
    return ", ".join(seen) if seen else "—"


# ── scoped stylesheet (offline; reuses theme tokens, adds only --gold) ────────────

_HALL_STYLE = """
<style>
.fishbowl.fb-hall { display:block; }
.fishbowl.fb-hall {
  --gold:#ffcf6b;            /* reuse the theme's --amber gold token */
  --gold-soft:rgba(255,207,107,0.18);
}
.fishbowl.fb-hall .hall-wrap { display:flex; flex-direction:column; gap:18px; }

/* Headline marquee — the killer demo line, big and glowing. */
.fishbowl.fb-hall .hall-headline {
  position:relative; overflow:hidden;
  padding:20px 26px; border-radius:16px;
  border:1px solid rgba(255,207,107,0.42);
  background:
    radial-gradient(120% 120% at 50% -20%, var(--gold-soft), transparent 60%),
    rgba(6,20,27,0.96);
  box-shadow:0 0 44px rgba(255,207,107,0.22), inset 0 1px 0 rgba(255,207,107,0.12);
}
.fishbowl.fb-hall .hall-headline .eyebrow { color:var(--gold); margin-bottom:8px; }
.fishbowl.fb-hall .hall-line {
  font-family:var(--font-display); font-weight:800;
  font-size:clamp(20px, 3vw, 34px); line-height:1.18;
  color:var(--ink);
  text-shadow:0 0 18px rgba(255,207,107,0.45);
}
.fishbowl.fb-hall .hall-line .hl-gold { color:var(--gold); }
/* A shimmer sweep across the headline (CSS-only). */
.fishbowl.fb-hall .hall-headline::after {
  content:""; position:absolute; inset:0; pointer-events:none;
  background:linear-gradient(105deg, transparent 30%,
    rgba(255,207,107,0.16) 48%, transparent 66%);
  transform:translateX(-120%);
  animation:hallShimmer 5.5s ease-in-out infinite;
}
@keyframes hallShimmer { 0%,55% { transform:translateX(-120%);} 78%,100% { transform:translateX(120%);} }

/* Empty states — cheerful, never a dead screen. */
.fishbowl.fb-hall .hall-empty {
  padding:22px 26px; border-radius:14px;
  border:1px dashed var(--line); background:var(--panel);
}
.fishbowl.fb-hall .hall-empty .eyebrow { color:var(--gold); margin-bottom:8px; }
.fishbowl.fb-hall .hall-empty .he-body { color:var(--ink-mid); line-height:1.55; }

/* Section panels reuse the theme's card chrome. */
.fishbowl.fb-hall .hall-panel {
  padding:18px 20px 20px; border-radius:var(--r-lg);
  border:1px solid var(--line); background:var(--panel);
  box-shadow:inset 0 1px 0 rgba(120,222,214,0.07), 0 16px 38px -32px rgba(0,0,0,0.9);
}
.fishbowl.fb-hall .hall-panel > .eyebrow { margin-bottom:14px; }

/* ── Podium (top 3): gold / silver / bronze phosphor ── */
.fishbowl.fb-hall .podium {
  display:grid; grid-template-columns:1fr 1.2fr 1fr; gap:14px; align-items:end;
  margin-bottom:18px;
}
.fishbowl.fb-hall .podium-slot {
  display:flex; flex-direction:column; align-items:center; gap:8px;
  padding:16px 12px; border-radius:14px; text-align:center;
  border:1px solid var(--line); background:rgba(6,20,27,0.7);
  animation:hallRise 0.5s cubic-bezier(0.2,0.9,0.3,1) both;
}
.fishbowl.fb-hall .podium-slot .ps-medal { font-size:30px; line-height:1; }
.fishbowl.fb-hall .podium-slot .ps-name {
  font-family:var(--font-display); font-weight:700; font-size:14px; color:var(--ink);
  word-break:break-word;
}
.fishbowl.fb-hall .podium-slot .ps-rate {
  font-family:var(--font-display); font-weight:800; font-variant-numeric:tabular-nums;
  font-size:22px;
}
.fishbowl.fb-hall .podium-slot .ps-sub { font-size:11px; color:var(--ink-dim); }
/* #1 — gold, taller, with a soft pulse. */
.fishbowl.fb-hall .podium-slot.rank-1 {
  border-color:rgba(255,207,107,0.5);
  background:radial-gradient(120% 120% at 50% -10%, var(--gold-soft), transparent 60%), rgba(6,20,27,0.92);
  box-shadow:0 0 40px rgba(255,207,107,0.3); padding-top:26px; animation-delay:0.05s;
}
.fishbowl.fb-hall .podium-slot.rank-1 .ps-rate { color:var(--gold); text-shadow:0 0 14px rgba(255,207,107,0.6); }
.fishbowl.fb-hall .podium-slot.rank-1 .ps-medal { animation:hallChampPulse 2.4s ease-in-out infinite; }
.fishbowl.fb-hall .podium-slot.rank-2 {
  border-color:rgba(159,220,210,0.45); animation-delay:0.16s;
}
.fishbowl.fb-hall .podium-slot.rank-2 .ps-rate { color:var(--ink-mid); }
.fishbowl.fb-hall .podium-slot.rank-3 {
  border-color:rgba(255,143,125,0.4); animation-delay:0.27s;
}
.fishbowl.fb-hall .podium-slot.rank-3 .ps-rate { color:var(--coral); }

/* ── Ranked table with glowing win-rate bars ── */
.fishbowl.fb-hall .hall-table { display:flex; flex-direction:column; gap:2px; }
.fishbowl.fb-hall .ht-head, .fishbowl.fb-hall .ht-row {
  display:grid; grid-template-columns:46px 1.6fr 0.7fr 0.7fr 1.5fr; gap:12px;
  align-items:center; padding:9px 12px; border-radius:var(--r);
}
.fishbowl.fb-hall .ht-head {
  font-family:var(--font-display); font-size:8.5px; letter-spacing:0.2em;
  text-transform:uppercase; color:var(--ink-faint);
  border-bottom:1px solid var(--line-soft); border-radius:0;
}
.fishbowl.fb-hall .ht-row { animation:hallRise 0.45s ease both; }
.fishbowl.fb-hall .ht-row:hover { background:var(--glass); }
.fishbowl.fb-hall .ht-row .ht-rank {
  font-family:var(--font-display); font-weight:700; color:var(--ink-dim);
  font-variant-numeric:tabular-nums;
}
.fishbowl.fb-hall .ht-row.is-champ .ht-rank { color:var(--gold); }
.fishbowl.fb-hall .ht-row .ht-model {
  font-family:var(--font-display); font-weight:600; color:var(--ink); word-break:break-word;
}
.fishbowl.fb-hall .ht-row .ht-num { font-variant-numeric:tabular-nums; color:var(--ink-mid); }
.fishbowl.fb-hall .ht-bar {
  position:relative; height:14px; border-radius:999px;
  background:rgba(120,222,214,0.08); overflow:hidden;
}
.fishbowl.fb-hall .ht-bar .ht-fill {
  position:absolute; inset:0 auto 0 0; height:100%; border-radius:999px;
  background:linear-gradient(90deg, rgba(255,207,107,0.55), var(--gold));
  box-shadow:0 0 12px rgba(255,207,107,0.45);
}
.fishbowl.fb-hall .ht-bar .ht-bar-label {
  position:absolute; right:8px; top:50%; transform:translateY(-50%);
  font-family:var(--font-display); font-size:9px; font-weight:700;
  color:var(--bg-0); text-shadow:0 0 3px rgba(255,207,107,0.8);
  font-variant-numeric:tabular-nums;
}

/* ── Sessions table ── */
.fishbowl.fb-hall .sess-head, .fishbowl.fb-hall .sess-row {
  display:grid; grid-template-columns:0.7fr 1.4fr 1.1fr 0.9fr 0.7fr 0.9fr;
  gap:10px; align-items:center; padding:9px 12px; border-radius:var(--r);
}
.fishbowl.fb-hall .sess-head {
  font-family:var(--font-display); font-size:8.5px; letter-spacing:0.2em;
  text-transform:uppercase; color:var(--ink-faint);
  border-bottom:1px solid var(--line-soft); border-radius:0;
}
.fishbowl.fb-hall .sess-row:hover { background:var(--glass); }
.fishbowl.fb-hall .sess-row .sr-id {
  font-family:var(--font-display); color:var(--ink-dim); font-size:12px;
}
.fishbowl.fb-hall .sess-row .sr-cast { color:var(--ink-mid); font-size:12px; word-break:break-word; }
.fishbowl.fb-hall .sess-row .sr-num { font-variant-numeric:tabular-nums; color:var(--ink-mid); }
.fishbowl.fb-hall .sr-won {
  display:inline-flex; align-items:center; gap:5px;
  font-family:var(--font-display); font-size:11px; font-weight:700;
  text-transform:uppercase; letter-spacing:0.08em;
  color:var(--gold); text-shadow:0 0 10px rgba(255,207,107,0.5);
}
.fishbowl.fb-hall .sr-reason { color:var(--ink-faint); font-size:11px; }

/* ── Fairness footnote panel ── */
.fishbowl.fb-hall .fair-grid {
  display:grid; grid-template-columns:1.4fr 0.7fr 0.7fr 1.5fr; gap:10px;
  align-items:center; padding:8px 12px; border-radius:var(--r);
}
.fishbowl.fb-hall .fair-head {
  font-family:var(--font-display); font-size:8.5px; letter-spacing:0.2em;
  text-transform:uppercase; color:var(--ink-faint);
  border-bottom:1px solid var(--line-soft); border-radius:0;
}
.fishbowl.fb-hall .fair-grid:hover { background:var(--glass); }
.fishbowl.fb-hall .fair-seat { font-family:var(--font-display); font-weight:600; color:var(--ink); }
.fishbowl.fb-hall .fair-num { font-variant-numeric:tabular-nums; color:var(--ink-mid); }
.fishbowl.fb-hall .fair-bar { position:relative; height:10px; border-radius:999px;
  background:rgba(120,222,214,0.08); overflow:hidden; }
.fishbowl.fb-hall .fair-bar .fair-fill { position:absolute; inset:0 auto 0 0; height:100%;
  border-radius:999px; background:linear-gradient(90deg, var(--cyan), var(--teal));
  box-shadow:0 0 8px rgba(79,230,210,0.4); }
.fishbowl.fb-hall .fair-note {
  margin-top:14px; padding:10px 12px; border-radius:var(--r);
  border:1px solid var(--line-soft); background:rgba(255,207,107,0.06);
  color:var(--ink-dim); font-size:11px; line-height:1.5;
}

@keyframes hallRise { from { opacity:0; transform:translateY(8px);} to { opacity:1; transform:none;} }
@keyframes hallChampPulse {
  0%,100% { transform:scale(1); filter:drop-shadow(0 0 6px rgba(255,207,107,0.5)); }
  50% { transform:scale(1.12); filter:drop-shadow(0 0 14px rgba(255,207,107,0.85)); }
}
@media (prefers-reduced-motion: reduce) {
  .fishbowl.fb-hall .hall-headline::after,
  .fishbowl.fb-hall .podium-slot,
  .fishbowl.fb-hall .podium-slot.rank-1 .ps-medal,
  .fishbowl.fb-hall .ht-row { animation:none !important; }
}
</style>
"""


# ── render functions (pure HTML strings, defensive, .fishbowl-scoped) ─────────────


def _wrap(inner: str) -> str:
    """Wrap a Hall-of-Fame pane in the ``.fishbowl.fb-hall`` scope root.

    Mirrors :func:`src.ui.fishbowl.app._fishbowl` so the theater stylesheet (and the
    inlined Hall-of-Fame ``<style>``) win over Gradio's cascade.
    """
    return f'<div class="fishbowl fb-hall">{_HALL_STYLE}{inner}</div>'


def render_headline(entries) -> str:
    """The big glowing demo line from :func:`leaderboard.headline`, or an empty state.

    Renders e.g. ``MiniCPM-8B beats Gemma-12B · 7-3 at Debate Duel`` with the two model
    names gilded.  When ``headline`` returns ``None`` (no symmetric scenario with two
    models that have each won), shows a cheerful "no champions yet" call to action.
    """
    try:
        line = headline(entries)
    except Exception:  # pragma: no cover - projection error (defensive)
        line = None
    if not line:
        return _wrap(
            '<div class="hall-empty">'
            '<div class="eyebrow">&#127942; Hall of Fame</div>'
            '<div class="he-body">No champions crowned yet — run a competitive scenario '
            "(a versus duel or a judged contest) to fill the Hall.</div>"
            "</div>"
        )
    # Gild the two model names: the line is "<A> beats <B> · X-Y at <Scenario>".
    safe = _esc(line)
    if " beats " in line:
        left, _, rest = line.partition(" beats ")
        runner, _, tail = rest.partition(" · ")
        safe = (
            f'<span class="hl-gold">{_esc(left)}</span> beats '
            f'<span class="hl-gold">{_esc(runner)}</span> · {_esc(tail)}'
        )
    return _wrap(
        '<div class="hall-headline">'
        '<div class="eyebrow">&#127942; Today\'s Champion</div>'
        f'<div class="hall-line">{safe}</div>'
        "</div>"
    )


def _podium_html(rows: list[ModelRow]) -> str:
    """The gold/silver/bronze top-3 podium (visual order: 2nd · 1st · 3rd)."""
    if not rows:
        return ""
    top = rows[:3]
    medals = {0: "&#129351;", 1: "&#129352;", 2: "&#129353;"}  # 🥇 🥈 🥉

    def slot(idx: int) -> str:
        if idx >= len(top):
            return '<div class="podium-slot" style="visibility:hidden"></div>'
        row = top[idx]
        return (
            f'<div class="podium-slot rank-{idx + 1}">'
            f'<div class="ps-medal">{medals.get(idx, "")}</div>'
            f'<div class="ps-name">{_esc(_short_model(row.model))}</div>'
            f'<div class="ps-rate">{_pct(row.win_rate)}</div>'
            f'<div class="ps-sub">{row.wins}/{row.plays} won</div>'
            "</div>"
        )

    # Visual order puts #1 in the centre, raised.
    order = [1, 0, 2]
    return '<div class="podium">' + "".join(slot(i) for i in order) + "</div>"


def render_model_board(entries) -> str:
    """The model podium + ranked table with glowing gold win-rate bars.

    Folds :func:`leaderboard.model_table` (already sorted by win-rate) into the podium
    for the top 3 and a ranked table below.  Each row carries rank, model, plays, wins
    and a horizontal bar whose width is the win-rate.  Empty store → empty state.
    """
    try:
        rows = model_table(entries)
    except Exception:  # pragma: no cover - projection error (defensive)
        rows = []
    if not rows:
        return _wrap(
            '<div class="hall-empty">'
            '<div class="eyebrow">&#128202; Model Leaderboard</div>'
            '<div class="he-body">No decided competitive runs yet. Once a versus or judged '
            "scenario crowns a winner, the models climb the board here.</div>"
            "</div>"
        )
    head = '<div class="ht-head"><div>#</div><div>Model</div><div>Plays</div><div>Wins</div><div>Win rate</div></div>'
    body_rows: list[str] = []
    for idx, row in enumerate(rows):
        champ = " is-champ" if idx == 0 else ""
        width = _bar_width(row.win_rate)
        body_rows.append(
            f'<div class="ht-row{champ}" style="animation-delay:{min(idx, 12) * 0.04:.2f}s">'
            f'<div class="ht-rank">{idx + 1}</div>'
            f'<div class="ht-model">{_esc(_short_model(row.model))}</div>'
            f'<div class="ht-num">{row.plays}</div>'
            f'<div class="ht-num">{row.wins}</div>'
            '<div class="ht-bar">'
            f'<div class="ht-fill" style="width:{width:.1f}%"></div>'
            f'<div class="ht-bar-label">{_pct(row.win_rate)}</div>'
            "</div>"
            "</div>"
        )
    return _wrap(
        '<div class="hall-panel">'
        '<div class="eyebrow">&#128202; Model Leaderboard</div>'
        + _podium_html(rows)
        + '<div class="hall-table">'
        + head
        + "".join(body_rows)
        + "</div></div>"
    )


def render_sessions(entries, scenario_name: str) -> str:
    """The sessions table for *scenario_name* (without the Replay buttons).

    Lists each finished, won, competitive run newest-first: a short id, the cast's
    models, the gold WON badge, why it ended, turns/tokens and the date.  The Replay
    buttons themselves are real Gradio components rendered alongside this HTML by
    ``build_hall_of_fame`` (gr.HTML can't host working buttons); this pane is the
    glanceable record.  Empty / non-competitive scenario → empty state.
    """
    if not scenario_name:
        return _wrap(
            '<div class="hall-empty">'
            '<div class="eyebrow">&#9654; Sessions</div>'
            '<div class="he-body">Pick a competitive world above to see its decided '
            "matches and replay any of them.</div>"
            "</div>"
        )
    try:
        rows = scenario_sessions(entries, scenario_name)
    except Exception:  # pragma: no cover - projection error (defensive)
        rows = []
    title = _esc(_scenario_title(scenario_name))
    if not rows:
        return _wrap(
            '<div class="hall-panel">'
            f'<div class="eyebrow">&#9654; {title} · Sessions</div>'
            '<div class="hall-empty" style="border:none;background:transparent;padding:8px 0">'
            '<div class="he-body">No decided matches in this world yet. Run it as a '
            "competition and the results land here, replayable.</div>"
            "</div></div>"
        )
    head = (
        '<div class="sess-head">'
        "<div>Run</div><div>Cast models</div><div>Winner</div>"
        "<div>Why</div><div>Turns</div><div>When</div>"
        "</div>"
    )
    body_rows = "".join(_session_row_html(row) for row in rows)
    return _wrap(
        '<div class="hall-panel">'
        f'<div class="eyebrow">&#9654; {title} · Sessions</div>'
        f'<div class="hall-table">{head}{body_rows}</div>'
        "</div>"
    )


def _session_row_html(row: LeaderboardEntry) -> str:
    """One sessions-table row (glanceable; the Replay button sits beside it)."""
    reason = _REASON_LABEL.get(row.reason or "", row.reason or "—")
    won = (
        f'<span class="sr-won">&#127942; {_esc(row.winner)}</span>'
        if row.winner
        else '<span class="sr-reason">—</span>'
    )
    return (
        '<div class="sess-row">'
        f'<div class="sr-id">{_esc(_short_id(row.run_id))}</div>'
        f'<div class="sr-cast">{_esc(_cast_models(row))}</div>'
        f"<div>{won}</div>"
        f'<div class="sr-reason">{_esc(reason)}</div>'
        f'<div class="sr-num">{row.turns}t · {_esc(_fmt_tokens(row.tokens))}</div>'
        f'<div class="sr-num">{_esc(_fmt_when(row.finished_at))}</div>'
        "</div>"
    )


def render_fairness(entries, scenario_name: str) -> str:
    """The 6.3 fairness footnote: win rate per seat type, with the asymmetry note.

    Folds :func:`leaderboard.fairness_table` (only *declared* seats — teams /
    symmetric seats; judges and other unmapped seats are excluded by design).  A
    footnote reminds viewers that asymmetric seats (spy vs herd) skew raw win rates.
    Empty → a quiet empty state.
    """
    if not scenario_name:
        return ""
    try:
        rows = fairness_table(entries, scenario_name)
    except Exception:  # pragma: no cover - projection error (defensive)
        rows = []
    if not rows:
        return _wrap(
            '<div class="hall-panel">'
            '<div class="eyebrow">&#9878;&#65039; Seat Fairness</div>'
            '<div class="he-body" style="color:var(--ink-mid)">No per-seat data yet — '
            "this footnote fills in once this world has decided competitive runs.</div>"
            "</div>"
        )
    head = (
        '<div class="fair-grid fair-head"><div>Seat type</div><div>Plays</div><div>Wins</div><div>Win rate</div></div>'
    )
    body = "".join(_fairness_row_html(row) for row in rows)
    note = (
        '<div class="fair-note">&#9888;&#65039; Seats are not always symmetric — a spy '
        "faces a whole herd, a lone debater a panel. Raw seat win rates show the structural "
        "tilt of the game, not just who played well.</div>"
    )
    return _wrap(
        '<div class="hall-panel">'
        '<div class="eyebrow">&#9878;&#65039; Seat Fairness</div>'
        f'<div class="hall-table">{head}{body}</div>'
        f"{note}</div>"
    )


def _fairness_row_html(row: SeatRow) -> str:
    """One fairness row: seat type, plays, wins, and a cyan win-rate bar."""
    width = _bar_width(row.win_rate)
    return (
        '<div class="fair-grid">'
        f'<div class="fair-seat">{_esc(row.seat_type)}</div>'
        f'<div class="fair-num">{row.plays}</div>'
        f'<div class="fair-num">{row.wins}</div>'
        '<div class="fair-bar">'
        f'<div class="fair-fill" style="width:{width:.1f}%"></div>'
        "</div>"
        "</div>"
    )


def _scenario_title(scenario_name: str) -> str:
    """The display title for an internal scenario name, falling back to the name."""
    try:
        cfg = default_registry().scenarios.get(scenario_name)
        return (getattr(cfg, "title", "") or scenario_name) if cfg else scenario_name
    except Exception:  # pragma: no cover - registry unavailable (defensive)
        return scenario_name


# ── builder ──────────────────────────────────────────────────────────────────────


def build_hall_of_fame() -> dict:
    """Lay out the Hall of Fame tab and return a handles dict for the app to wire.

    Layout (top → bottom): the glowing **headline marquee**, a **scenario picker**
    (only competitive worlds), the **model podium + ranked table**, the per-scenario
    **sessions area** (an HTML record + a ``gr.render`` of per-run Replay buttons),
    the **fairness footnote**, and a **refresh** control.

    Returns a handles dict the app wires into the Show transport:

    ``{"scenario_dd", "refresh", "headline_html", "model_html", "sessions_html",
       "fairness_html", "sessions_render_anchor", "default_scenario"}``

    The Replay buttons are *not* built here — they need the Show's pane handles, which
    only exist after the Show tab builds.  The app calls :func:`wire_sessions_render`
    (below) with the shared ``archive_refs`` + states after all tabs build, mirroring
    how ``_build_archive_drawer`` defers its ``show_handles`` lookup.
    """
    scenarios = competitive_scenarios()
    choices = [(title, name) for title, name in scenarios]
    default_scenario = choices[0][1] if choices else None
    entries = _entries()

    handles: dict = {"default_scenario": default_scenario}

    with gr.Column(elem_classes=["hall-wrap"]):
        handles["headline_html"] = gr.HTML(render_headline(entries))

        handles["scenario_dd"] = gr.Dropdown(
            choices=choices,
            value=default_scenario,
            label="World",
            elem_classes=["hall-scenario"],
            interactive=True,
            visible=bool(choices),
        )
        handles["refresh"] = gr.Button("⟳ refresh", size="sm", scale=0)

        handles["model_html"] = gr.HTML(render_model_board(entries))

        handles["sessions_html"] = gr.HTML(render_sessions(entries, default_scenario))
        # The per-run Replay buttons live in this column; the app fills it with a
        # gr.render (it needs the Show panes, populated after all tabs build).
        handles["sessions_render_anchor"] = gr.Column(elem_classes=["hall-replays"])

        handles["fairness_html"] = gr.HTML(render_fairness(entries, default_scenario))

    return handles


def wire_sessions_render(handles: dict, *, refs: dict, tabs, states: dict) -> None:
    """Wire the Hall's data refresh + the per-run Replay buttons into the Show.

    Called by the app **after all tabs build** so ``refs["show_handles"]`` is live
    (mirrors ``_build_archive_drawer``'s deferred-ref trick).  Three behaviours:

    * Changing the scenario picker (or refresh) re-reads the ledger and repaints the
      model board, sessions table and fairness panel.
    * A ``gr.render`` keyed on (scenario, refresh) lists per-run **Replay** buttons.
    * Each Replay click loads the run via :func:`load_replay` and pushes it into the
      Show — the *exact* output contract from ``_build_archive_drawer`` (session, k,
      scenario, switch Tabs to "show", repaint panes, stopped=False, ticks=0).

    Defensive throughout: missing handles or an unavailable store degrade to no-ops /
    empty states rather than raising.
    """
    if not handles:
        return
    # Late imports of the app's render/replay helpers — they live in app.py and importing
    # them at module top would create an import cycle (app imports this module).
    try:
        from src.ui.fishbowl.app import (
            _pad_values,
            _registry,
            _render_at,
            _show_outs,
            _title_for,
            _tools,
            load_replay,
        )
    except Exception:  # pragma: no cover - app helpers unavailable (defensive)
        return

    scenario_dd = handles.get("scenario_dd")
    refresh = handles.get("refresh")
    model_html = handles.get("model_html")
    sessions_html = handles.get("sessions_html")
    fairness_html = handles.get("fairness_html")
    anchor = handles.get("sessions_render_anchor")
    if scenario_dd is None:
        return

    # ── refresh the boards on scenario change / refresh click ──────────────────────
    def _repaint(scenario_name):
        entries = _entries()
        return (
            render_model_board(entries),
            render_sessions(entries, scenario_name),
            render_fairness(entries, scenario_name),
        )

    repaint_outputs = [c for c in (model_html, sessions_html, fairness_html) if c is not None]
    if repaint_outputs:
        # Always emit a full tuple, but only route to present panes (defensive padding).
        def _repaint_present(scenario_name):
            full = _repaint(scenario_name)
            picked = []
            for comp, value in zip((model_html, sessions_html, fairness_html), full):
                if comp is not None:
                    picked.append(value)
            return tuple(picked) if len(picked) != 1 else picked[0]

        scenario_dd.change(_repaint_present, inputs=[scenario_dd], outputs=repaint_outputs)
        if refresh is not None:
            refresh.click(_repaint_present, inputs=[scenario_dd], outputs=repaint_outputs)

    # ── per-run Replay buttons (gr.render → Show transport) ────────────────────────
    if anchor is None:
        return

    with anchor:

        @gr.render(
            inputs=[scenario_dd],
            triggers=[scenario_dd.change] + ([refresh.click] if refresh is not None else []),
        )
        def _render_replays(scenario_name):
            if not scenario_name:
                return
            entries = _entries()
            try:
                rows = scenario_sessions(entries, scenario_name)
            except Exception:  # pragma: no cover - projection error (defensive)
                rows = []
            if not rows:
                return

            show_outs = _show_outs(refs.get("show_handles") or {})
            n_out = 6 + len(show_outs)  # session, k, scenario, tabs, *panes, stopped, ticks

            def _loader(run_id: str):
                def _load(layout, mind_reader):
                    session = load_replay(run_id, registry=_registry, tools=_tools)
                    if session is None:
                        return tuple(gr.update() for _ in range(n_out))
                    k = session.head  # land on the full discussion; ▶ replays it
                    out = _render_at(session, k, layout=layout, mind_reader=mind_reader)
                    return (
                        session,
                        k,
                        _title_for(session.scenario_name),
                        gr.update(selected="show"),
                        *_pad_values(out, show_outs),
                        False,
                        0,
                    )

                return _load

            for row in rows:
                label = f"▶ Replay {_short_id(row.run_id)}"
                if row.winner:
                    label += f"  ·  WON {row.winner}"
                btn = gr.Button(label, elem_classes=["archive-card"], size="sm")
                btn.click(
                    _loader(row.run_id),
                    inputs=[states["layout"], states["mind"]],
                    outputs=[
                        states["session"],
                        states["k"],
                        states["scenario"],
                        tabs,
                        *show_outs,
                        states["stopped"],
                        states["ticks"],
                    ],
                )


__all__ = [
    "build_hall_of_fame",
    "competitive_scenarios",
    "render_fairness",
    "render_headline",
    "render_model_board",
    "render_sessions",
    "wire_sessions_render",
]
