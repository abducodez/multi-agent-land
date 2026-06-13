"""FISHBOWL — the Gradio app shell (Unit 9: integrator + root shim).

Two tabs — **The Lab** (compose a run) and **The Show** (watch the say-vs-think
MindCard replay) — wrapped in the CRT theater chrome.  This module owns the app shell
and *all* cross-module wiring: it holds the per-user session in ``gr.State``, drives the
hybrid transport (scrub-back = pure prefix view; play-at-head = step the Conductor), and
composes the Show's stage/feed/meters/verdict HTML from the render units.

Every sibling unit (theme, render.*, session, show, lab) is imported **defensively**:
this worker runs in its own worktree before the leaf modules land, so each import falls
back to a friendly placeholder.  The shell therefore builds and launches standalone, and
upgrades itself the moment the real modules are merged — no edits required here.

Offline-first: no API key is needed; the deterministic stub keeps the demo reproducible.
"""

from __future__ import annotations

import os
import socket

import gradio as gr

# ── engine read surface (public; never mutated here) ───────────────────────────
from src.core.conductor import Conductor
from src.core.governor import BudgetExceeded
from src.core.ledger_factory import make_ledger
from src.core.registry import default_registry
from src.tools.builtins import default_tool_registry
from src.ui.fishbowl.adapter import scenario_voice
from src.ui.fishbowl.view_model import view_model_at

try:  # archive: "my past sessions" read layer over the run-history ledger (ADR-0026)
    from src.ui.fishbowl.archive import list_runs, load_replay, run_card_label
except Exception:  # pragma: no cover - degrade gracefully if the archive unit is absent

    def list_runs(*_args, **_kwargs):
        return []

    def load_replay(*_args, **_kwargs):
        return None

    def run_card_label(_summary):
        return "▶ (run)"


# ── loop-safety backstop ────────────────────────────────────────────────────────
# Belt-and-suspenders against a runaway autoplay loop: even when the governor never
# trips (e.g. a generous budget) the timer halts after this many consecutive auto-ticks
# per play session.  The user explicitly asked for "no infinite, token-burning loop".
_MAX_AUTO_TICKS = 40

# ── defensive imports of sibling units ─────────────────────────────────────────
# Each block degrades to a placeholder so the shell runs before the leaf modules land.

try:  # theme: CSS + <head> + a gr.Theme
    from src.ui.fishbowl.theme import FISHBOWL_HEAD, FishbowlTheme, load_css
except Exception:  # pragma: no cover - exercised only before the theme unit lands

    def load_css() -> str:
        # Minimal CRT-ish palette so the placeholder shell is legible.
        return """
        :root { --bg:#070a06; --ink:#cfe8c0; --lime:#8fe36a; --cyan:#5fd0d0;
                --violet:#b59cff; --amber:#e3c14c; --coral:#e3786a; }
        body { background: var(--bg); color: var(--ink);
               font-family: 'IBM Plex Mono', ui-monospace, monospace; }
        footer { display: none !important; }
        .fishbowl-topbar { display:flex; align-items:baseline; gap:12px;
            padding:10px 16px; border-bottom:1px solid #2e3d25; }
        .fishbowl-topbar .logo { color: var(--lime); font-weight:800; letter-spacing:.08em; }
        .fishbowl-topbar .sub { color: var(--cyan); font-style:italic; opacity:.8; }
        .crt-bg, .crt-grid, .crt-scan, .crt-vignette {
            position: fixed; inset: 0; pointer-events: none; z-index: 0; }
        .crt-vignette { box-shadow: inset 0 0 220px rgba(0,0,0,.8); }
        .fishbowl-placeholder { padding: 24px; color: var(--ink);
            border:1px dashed #2e3d25; border-radius:10px; line-height:1.6; }
        """

    FISHBOWL_HEAD = (
        '<link rel="preconnect" href="https://fonts.googleapis.com" />'
        '<link href="https://fonts.googleapis.com/css2?family=Martian+Mono:wght@400;700;800'
        '&family=IBM+Plex+Mono:ital,wght@0,400;0,500;1,400&display=swap" rel="stylesheet" />'
    )
    FishbowlTheme = None  # launch() tolerates theme=None

try:  # render: the HTML pieces of the Show
    from src.ui.fishbowl.render.mindcard import render_mindcard
except Exception:  # pragma: no cover

    def render_mindcard(card, *, mind_reader: bool = False) -> str:
        name = card.get("name", "?")
        said = card.get("said") or "…"
        body = said
        if mind_reader and card.get("thought"):
            body += f"<br/><span style='opacity:.7;font-style:italic'>“{card['thought']}”</span>"
        return f"<div class='mind' data-id='{card.get('id', '')}'><b>{name}</b><br/>{body}</div>"


try:
    # render_constellation lives in render.stage alongside render_split.
    from src.ui.fishbowl.render.stage import render_constellation
except Exception:  # pragma: no cover

    def render_constellation(vm, cards_html_by_id) -> str:
        cards = "".join(cards_html_by_id.values())
        return f"<div class='constellation'>{cards}</div>"


try:
    from src.ui.fishbowl.render.stage import render_split
except Exception:  # pragma: no cover

    def render_split(vm) -> str:
        rows = "".join(
            f"<tr><td>{c.get('name', '?')}</td><td>{c.get('said') or ''}</td>"
            f"<td><i>{c.get('thought') or ''}</i></td></tr>"
            for c in vm.get("cast", [])
        )
        return f"<table class='split'><tbody>{rows}</tbody></table>"


try:
    from src.ui.fishbowl.render.feed import render_feed
except Exception:  # pragma: no cover

    def render_feed(vm, *, mind_reader: bool = False) -> str:
        items = []
        for it in vm.get("feed", []):
            kind = it.get("kind", "")
            if kind == "say":
                line = f"<b>{it.get('agent', '?')}</b>: {it.get('said') or ''}"
                if mind_reader and it.get("thought"):
                    line += f" <i style='opacity:.7'>({it['thought']})</i>"
            elif kind == "narrate":
                line = f"<i>{it.get('text', '')}</i>"
            elif kind == "poke":
                line = f"<b>[{it.get('label', 'POKE')}]</b> {it.get('text', '')}"
            elif kind == "verdict":
                line = f"<b>VERDICT:</b> {it.get('text', '')}"
            else:
                line = it.get("text", "")
            items.append(f"<div class='feed-item feed-{kind}'>{line}</div>")
        return f"<div class='feed'>{''.join(items)}</div>"


try:
    from src.ui.fishbowl.render.meters import render_meters
except Exception:  # pragma: no cover

    def render_meters(vm) -> str:
        return (
            "<div class='meters'>"
            f"<span>step {vm.get('step', 0)}/{vm.get('total', 0)}</span> · "
            f"<span>tokens {vm.get('tokens', 0)}</span> · "
            f"<span>round {vm.get('rounds', 1)}</span>"
            "</div>"
        )


try:
    # render_verdict lives in render.meters alongside render_meters.
    from src.ui.fishbowl.render.meters import render_verdict
except Exception:  # pragma: no cover

    def render_verdict(vm) -> str:
        v = vm.get("verdict")
        if not v:
            return ""
        return f"<div class='verdict'>{v.get('text', '')}</div>"


try:  # session: the transport wrapper over a Conductor
    from src.ui.fishbowl.session import FishbowlSession
except Exception:  # pragma: no cover - fallback session keeps the shell live

    class FishbowlSession:
        """Minimal transport over a Conductor (real session unit supersedes this).

        Mirrors the contract the integrator wires against: ``reset(seed)``,
        ``step()`` (generate at the head), ``inject(text, label=...)``, and
        ``snapshot(k)`` (a pure prefix view via ``view_model_at``)."""

        def __init__(self, scenario_name: str, *, registry=None, tools=None) -> None:
            reg = registry or default_registry()
            tool_reg = tools if tools is not None else default_tool_registry()
            self.conductor = Conductor(
                reg.build_scenario(scenario_name, tools=tool_reg),
                governor=reg.governor_for(scenario_name),
                ledger=make_ledger(),
            )
            self.scenario_name = scenario_name

        @property
        def head(self) -> int:
            return len(self.conductor.ledger.events)

        def has_verdict(self) -> bool:
            # Mirrors the real session's loop-safety helper so on_tick never crashes.
            return any(getattr(e, "kind", None) == "judge.verdict" for e in self.conductor.ledger.events)

        def reset(self, seed: str) -> None:
            self.conductor.reset(seed or self.conductor.scenario.default_seed)

        def step(self) -> None:
            self.conductor.step()

        def step_one(self) -> bool:
            return self.conductor.step_one()

        def inject(self, text: str, *, label: str | None = None) -> None:
            if text and text.strip():
                self.conductor.inject_user_event(text.strip(), label=label)
            self.conductor.step()

        def snapshot(self, k: int | None = None) -> dict:
            events = self.conductor.ledger.events
            kk = self.head if k is None else max(0, min(int(k), len(events)))
            scn = self.conductor.scenario
            # view_model_at wants AgentManifests; agents carry theirs on .manifest.
            cast = [getattr(a, "manifest", a) for a in scn.agents]
            return view_model_at(
                events,
                kk,
                cast,
                scenario_name=self.scenario_name,
                goal=getattr(scn, "goal", ""),
                governor=self.conductor.governor,
            )


try:  # the two tab bodies (leaf units own their internals)
    from src.ui.fishbowl.show import build_show
except Exception:  # pragma: no cover

    def build_show():
        gr.HTML(
            "<div class='fishbowl-placeholder'>The Show stage loads here once the <code>show</code> unit lands.</div>"
        )
        return {}


try:
    from src.ui.fishbowl.lab import build_lab
except Exception:  # pragma: no cover

    def build_lab():
        gr.HTML(
            "<div class='fishbowl-placeholder'>The Lab composer loads here once the <code>lab</code> unit lands.</div>"
        )
        return {}


def build_telemetry() -> None:
    """The Telemetry tab: structured log feed + metric charts + per-trace timeline.

    Reads live from the in-memory telemetry store (ADR-0024); a manual Refresh, a
    3s auto-tick, and the filter dropdowns all repaint it. Clicking a span in the
    timeline reveals the prompt + memory that agent saw.
    """
    try:
        from src.ui.fishbowl.render import telemetry as t
    except Exception:  # pragma: no cover - degrade to a friendly placeholder
        gr.HTML("<div class='fishbowl-placeholder'>Telemetry panel unavailable.</div>")
        return

    gr.HTML(f"<style>{t.TELEMETRY_CSS}</style>")
    kpi = gr.Markdown(t.kpi_markdown())
    with gr.Row():
        level_dd = gr.Dropdown(t.LEVELS, value="all", label="Level", scale=1)
        layer_dd = gr.Dropdown(t.LAYERS, value="all", label="Layer", scale=1)
        refresh = gr.Button("⟳ Refresh", scale=1)
    with gr.Row():
        calls_plot = gr.BarPlot(t.calls_frame(), x="metric", y="count", title="Activity", scale=1)
        tokens_plot = gr.BarPlot(t.tokens_frame(), x="kind", y="tokens", title="Tokens", scale=1)
        latency_plot = gr.LinePlot(
            t.latency_frame(), x="n", y="seconds", color="agent", title="Agent-turn latency (s)", scale=1
        )
    feed = gr.Dataframe(
        headers=["time", "level", "agent/turn", "event", "detail"],
        value=t.log_rows(),
        wrap=True,
        label="Structured log feed",
        row_count=(12, "dynamic"),
        column_count=(5, "fixed"),
    )
    traces = gr.HTML(t.traces_html())
    timer = gr.Timer(3.0)

    def _refresh(level, layer):
        return (
            t.kpi_markdown(),
            t.log_rows(level, layer),
            t.calls_frame(),
            t.tokens_frame(),
            t.latency_frame(),
            t.traces_html(),
        )

    outs = [kpi, feed, calls_plot, tokens_plot, latency_plot, traces]
    refresh.click(_refresh, [level_dd, layer_dd], outs)
    timer.tick(_refresh, [level_dd, layer_dd], outs)
    level_dd.change(_refresh, [level_dd, layer_dd], outs)
    layer_dd.change(_refresh, [level_dd, layer_dd], outs)


# ── scenario registry (assembled from config/, not hardcoded) ───────────────────

_registry = default_registry()
_tools = default_tool_registry()

_PREFERRED = [
    "thousand-token-wood",
    "mystery-roots",
    "oracle-grove",
    "the-steeped",
    "debate-duel",
    "twenty-sprouts",
    "beat-battle",
]
_names = [n for n in _PREFERRED if n in _registry.scenarios] + [
    n for n in sorted(_registry.scenarios) if n not in _PREFERRED
]
# display title -> internal scenario name
SCENARIOS: dict[str, str] = {(_registry.scenarios[n].title or n): n for n in _names}
_DEFAULT_TITLE = next(iter(SCENARIOS), "")

# Transport speeds → gr.Timer interval (seconds). Keys mirror the speed radio.
SPEEDS: dict[str, float] = {"live": 3.0, "1×": 1.9, "fast": 1.0}


# ── session lifecycle (a fresh Conductor + session per Summon) ──────────────────


def _new_session(scenario_name: str) -> FishbowlSession:
    # The session unit builds its own Conductor from the scenario name.
    return FishbowlSession(scenario_name, registry=_registry, tools=_tools)


def _empty_vm() -> dict:
    """A safe, empty view-model so the Show renders before the first Summon."""
    return {"step": 0, "total": 0, "cast": [], "feed": [], "verdict": None, "rounds": 1, "tokens": 0}


# ── HTML composition (this worker owns this) ────────────────────────────────────


def _fishbowl(inner: str, *, role: str) -> str:
    """Wrap a rendered pane in the ``.fishbowl`` scope root.

    Every theater rule in ``assets/styles.css`` is scoped under ``.fishbowl`` so it wins
    over Gradio's own cascade; the ``gr.HTML`` islands have no such ancestor on their own,
    so without this wrapper the MindCards/feed/meters render as unstyled stacked text.  The
    ``role`` adds a stable hook (e.g. ``fb-stage``) for layout rules that target a pane.
    """
    if not inner:
        return ""
    return f'<div class="fishbowl {role}">{inner}</div>'


def render_show_html(
    vm: dict, *, layout: str = "constellation", mind_reader: bool = False
) -> tuple[str, str, str, str]:
    """Compose the Show's four HTML panes from a view-model snapshot.

    Returns ``(stage, feed, meters, verdict)``.  The stage honours the layout radio:
    *constellation* (MindCards), *split* (omniscient table), or *feed* (feed-only).  Each
    pane is wrapped in a ``.fishbowl`` scope root so the theater stylesheet applies."""
    cards_html_by_id: dict[str, str] = {}
    for card in vm.get("cast", []):
        cards_html_by_id[card.get("id", card.get("name", ""))] = render_mindcard(card, mind_reader=mind_reader)

    if layout == "split":
        stage = render_split(vm)
    elif layout == "feed":
        stage = render_feed(vm, mind_reader=mind_reader)
    else:  # constellation (default)
        stage = render_constellation(vm, cards_html_by_id)

    feed = render_feed(vm, mind_reader=mind_reader)
    meters = render_meters(vm)
    verdict = render_verdict(vm)
    return (
        _fishbowl(stage, role=f"fb-stage fb-{layout}"),
        _fishbowl(feed, role="fb-feed"),
        _fishbowl(meters, role="fb-meters"),
        _fishbowl(verdict, role="fb-verdict"),
    )


def _render_at(session: FishbowlSession | None, k: int, *, layout: str, mind_reader: bool) -> tuple[str, str, str, str]:
    """Render the Show at play-head *k* (pure prefix view), or empty if no session."""
    vm = session.snapshot(k) if session is not None else _empty_vm()
    return render_show_html(vm, layout=layout, mind_reader=mind_reader)


def advance_one_tick(session: FishbowlSession | None, k: int, ticks: int, *, max_auto_ticks: int = _MAX_AUTO_TICKS):
    """Decide one autoplay tick without touching Gradio — the loop-safety core.

    Returns ``(new_k, new_ticks, stop_reason)``.  ``stop_reason`` is ``None`` while the
    show should keep playing and a human-readable string once autoplay must halt — on a
    verdict at the head (the show resolved), a tripped governor budget, or the
    ``max_auto_ticks`` backstop.  Replaying the existing prefix (k < head) is free and
    never counts toward the backstop; only *generating* ticks do.  This is the pure
    function the timer handler and the loop-safety tests both drive."""
    k = int(k or 0)
    ticks = int(ticks or 0)
    if session is None:
        return 0, 0, None
    if getattr(session, "replay", False):
        # A loaded past run: replay forward through the recorded prefix, then stop.
        # It owns no live engine, so generating is impossible — never spend a tick here.
        if k < session.head:
            return k + 1, ticks, None
        return k, ticks, "end of session — replay complete"
    if session.has_verdict():
        return k, ticks, "verdict reached — the show resolved"
    if k < session.head:
        return k + 1, ticks, None  # replay forward through the existing prefix
    if ticks >= max_auto_ticks:
        return k, ticks, f"autoplay tick cap {max_auto_ticks} reached"
    try:
        session.step_one()  # at the head → generate ONE agent (stream it, don't wait for the turn)
    except BudgetExceeded as exc:
        return session.head, ticks, (getattr(exc, "reason", None) or str(exc))
    return session.head, ticks + 1, None


def _stopped_banner_html(reason: str) -> str:
    """A ``⛔ STOPPED`` banner reusing the verdict-banner chrome (assets/styles.css).

    Surfaced in the verdict pane when the governor trips a budget bound or the
    autoplay backstop fires, so the run halts visibly instead of crashing the
    Gradio callback or burning tokens in a loop."""
    import html as _html

    text = _html.escape(reason or "budget exceeded")
    return _fishbowl(
        '<div class="verdict banner">'
        '<div class="eyebrow">&#9940; Stopped</div>'
        f'<div class="disp vb-text">{text}</div>'
        "</div>",
        role="fb-verdict",
    )


# ── CRT theater chrome ──────────────────────────────────────────────────────────


def _live_chip() -> str:
    """The topbar status chip — computed from the per-backend live-credential gates.

    Shows ``● LIVE · MODAL`` / ``● LIVE · HUGGING FACE`` (or ``MODAL+HF`` when both are
    configured, with the pulsing dot) naming which inference backend(s) can drive the
    cast, else ``OFFLINE · STUB`` so the demo is honest about which path is live.
    Offline-first: with no env vars the stub label shows."""
    from src.models import inference

    configured = inference.configured_backends()
    if configured:
        name = " + ".join(inference.backend_label(b).upper() for b in configured)
        return f'<span class="chip live"><span class="live-dot"></span>&#9679; LIVE &middot; {name}</span>'
    return '<span class="chip live"><span class="live-dot"></span>OFFLINE &middot; STUB</span>'


def _topbar_html() -> str:
    return f"""
<div class="fishbowl">
  <div class="topbar">
    <div class="brand">
      <span class="logo">&#9673; FISHBOWL</span>
      <span class="sub">a fishbowl of minds you can read</span>
    </div>
    <div class="topbar-status">
      {_live_chip()}
      <span class="topbar-tag eyebrow">small minds &middot; one ledger &middot; &le; 32B</span>
    </div>
  </div>
</div>
"""


_TOPBAR_HTML = _topbar_html()

# The CSS expects these overlay layers (ui/raw/Fishbowl.html).
_CRT_BG_HTML = '<div class="crt-bg"></div><div class="crt-grid"></div>'
_CRT_FG_HTML = '<div class="crt-scan"></div><div class="crt-vignette"></div>'


# ── app builder ─────────────────────────────────────────────────────────────────


def build_app() -> gr.Blocks:
    """Build the two-tab FISHBOWL theater (defensive about absent leaf modules)."""
    # Theme/css/head are applied at launch() time (Gradio 6 moved them off Blocks).
    with gr.Blocks(title="FISHBOWL — a fishbowl of minds you can read") as demo:
        # CRT background layers (behind everything).
        gr.HTML(_CRT_BG_HTML)
        # Branding top bar.
        gr.HTML(_TOPBAR_HTML)

        # ── per-user state ──────────────────────────────────────────────────────
        session_state = gr.State(None)  # FishbowlSession | None
        k_state = gr.State(0)  # play-head
        scenario_state = gr.State(_DEFAULT_TITLE)
        mind_reader_state = gr.State(False)
        layout_state = gr.State("constellation")
        blank_state = gr.State("")  # stand-in input when a leaf widget is absent
        stopped_state = gr.State(False)  # set once the run halts (budget/backstop/verdict)
        tick_count_state = gr.State(0)  # consecutive autoplay ticks (the 40-tick backstop)
        # Per-user session id: resolved from the browser's localStorage on load (see
        # _SESSION_ID_JS) so it survives reloads.  Stamped onto run.started so the
        # Archive can list "my sessions only"; a hidden carrier, never shown.
        session_id_box = gr.Textbox(value="", visible=False, elem_id="fb-session-id")

        # Populated after the tabs build, then read at gr.render runtime (post-load) so
        # the Lab's Archive drawer can target the Show's panes it lists into.
        archive_refs: dict = {}

        with gr.Tabs() as tabs:
            with gr.Tab("The Lab", id="lab"):
                lab_handles = build_lab()
                _build_archive_drawer(
                    scenario_handle=(lab_handles or {}).get("scenario"),
                    session_id_box=session_id_box,
                    refs=archive_refs,
                    tabs=tabs,
                    states={
                        "session": session_state,
                        "k": k_state,
                        "scenario": scenario_state,
                        "layout": layout_state,
                        "mind": mind_reader_state,
                        "stopped": stopped_state,
                        "ticks": tick_count_state,
                    },
                )
            with gr.Tab("The Show", id="show"):
                show_handles = build_show()
            with gr.Tab("Telemetry", id="telemetry"):
                build_telemetry()

        # CRT foreground layers (scanlines + vignette, above content, click-through).
        gr.HTML(_CRT_FG_HTML)

        # The Archive's gr.render runs client-side after build; by then show_handles
        # exists, so it reads the live panes through this ref.
        archive_refs["show_handles"] = show_handles or {}

        _wire(
            tabs=tabs,
            lab_handles=lab_handles or {},
            show_handles=show_handles or {},
            session_state=session_state,
            k_state=k_state,
            scenario_state=scenario_state,
            mind_reader_state=mind_reader_state,
            layout_state=layout_state,
            blank_state=blank_state,
            stopped_state=stopped_state,
            tick_count_state=tick_count_state,
            session_id_box=session_id_box,
        )

        # Resolve (or mint) the browser's session id once the page loads; updating the
        # hidden box fires its change, which re-renders the Archive list for this user.
        demo.load(None, None, [session_id_box], js=_SESSION_ID_JS)

    return demo


# ── wiring (the integrator's core job) ──────────────────────────────────────────


def _h(handles: dict, *names):
    """First present handle among *names* (leaf units may name things variously)."""
    for n in names:
        if n in handles and handles[n] is not None:
            return handles[n]
    return None


def _wire(
    *,
    tabs: gr.Tabs,
    lab_handles: dict,
    show_handles: dict,
    session_state: gr.State,
    k_state: gr.State,
    scenario_state: gr.State,
    mind_reader_state: gr.State,
    layout_state: gr.State,
    blank_state: gr.State,
    stopped_state: gr.State,
    tick_count_state: gr.State,
    session_id_box: gr.Textbox | None = None,
) -> None:
    """Connect Lab/Show component handles to session transport + HTML re-render.

    Every handle lookup is defensive: if a leaf unit doesn't expose a widget yet, the
    corresponding wiring is simply skipped so the shell still builds and runs."""
    # Show output panes (rendered HTML).
    stage_out = _h(show_handles, "stage", "stage_html", "constellation")
    feed_out = _h(show_handles, "feed", "feed_html")
    meters_out = _h(show_handles, "meters", "meters_html")
    verdict_out = _h(show_handles, "verdict", "verdict_html")
    show_outs = [c for c in (stage_out, feed_out, meters_out, verdict_out) if c is not None]

    # Transport controls (looked up early so output lists can include the timer).
    timer = _h(show_handles, "timer")
    # The "halt tail" appended to advancing handlers' outputs: stop the timer (when one
    # exists) + record the stopped flag.  ``_halt_tail``/``_run_tail`` keep the returned
    # tuple aligned to whichever of these outputs are wired.
    _tail_outs = ([timer] if timer is not None else []) + [stopped_state]

    def _halt_tail() -> tuple:
        """Tail values that STOP autoplay: (timer active=False?, stopped=True)."""
        return ((gr.update(active=False),) if timer is not None else ()) + (True,)

    def _run_tail(stopped: bool = False) -> tuple:
        """Tail values that leave the timer untouched: (timer no-op?, stopped flag)."""
        return ((gr.update(),) if timer is not None else ()) + (stopped,)

    def _stopped_panes(session, k: int, *, layout: str, mind_reader: bool, reason: str) -> tuple:
        """Render the Show at *k* but swap the verdict pane for the STOPPED banner.

        Returns a tuple aligned to ``show_outs`` (verdict pane last when present), so
        the run halts *visibly* without crashing the callback."""
        panes = list(_render_at(session, k, layout=layout, mind_reader=mind_reader))
        banner = _stopped_banner_html(reason)
        if verdict_out is not None:
            panes[3] = banner  # the 4th pane (stage, feed, meters, verdict) is the verdict
        elif feed_out is not None:
            # No verdict pane wired — surface the halt in the feed so it is never silent.
            panes[1] = banner
        return _pad_values(tuple(panes), show_outs)

    # Show transport controls.
    scrubber = _h(show_handles, "scrubber", "slider", "scrub", "step_slider")
    play_btn = _h(show_handles, "play", "play_btn")
    step_btn = _h(show_handles, "step", "step_btn", "next", "advance", "fwd_btn")
    back_btn = _h(show_handles, "rewind", "back", "to_start", "first", "back_btn")
    speed_radio = _h(show_handles, "speed", "speed_radio")
    layout_radio = _h(show_handles, "layout", "layout_radio")
    mind_toggle = _h(show_handles, "mind_reader", "read_minds", "minds")
    poke_send = _h(show_handles, "poke_send", "poke_btn", "poke")
    poke_text = _h(show_handles, "poke_text", "poke_input")
    poke_buttons = show_handles.get("poke_buttons") or show_handles.get("poke_btns") or []

    # Lab controls.
    summon_btn = _h(lab_handles, "summon", "summon_btn", "launch", "start")
    scenario_in = _h(lab_handles, "scenario", "scenario_select", "scenario_dropdown")
    seed_in = _h(lab_handles, "seed", "seed_in", "world_seed")
    # Composer inputs — the per-cast Modal model picks (cast_models) and the run knobs;
    # all looked up defensively so the shell still runs if the Lab omits a widget.
    premise_in = _h(lab_handles, "premise")
    cast_models_in = _h(lab_handles, "cast_models")
    backend_in = _h(lab_handles, "inference_backend", "backend")
    judge_model_in = _h(lab_handles, "judge_model")
    judge_policy_in = _h(lab_handles, "judge_policy")
    judge_strictness_in = _h(lab_handles, "judge_strictness")
    tools_in = _h(lab_handles, "tools")
    # New editable surface: per-agent edit States + the scenario roster/genesis/governor.
    cast_tools_in = _h(lab_handles, "cast_tools")
    cast_personas_in = _h(lab_handles, "cast_personas")
    cast_schedules_in = _h(lab_handles, "cast_schedules")
    cast_roster_in = _h(lab_handles, "cast_roster")
    genesis_in = _h(lab_handles, "world")
    max_turns_in = _h(lab_handles, "max_turns")
    max_calls_in = _h(lab_handles, "max_calls_per_turn")
    max_tokens_in = _h(lab_handles, "max_total_tokens")
    hourly_budget_in = _h(lab_handles, "hourly_budget_usd")

    def _scenario_title(value) -> str:
        """Resolve a Lab scenario selection (title or internal name) to a title key."""
        if value in SCENARIOS:
            return value
        for title, name in SCENARIOS.items():
            if value == name:
                return title
        return _DEFAULT_TITLE

    def _compose_session(name, **knobs):
        """Build a session for a Lab-composed run: the selected Modal models drive the
        cast (ADR-0022).  The composed WorldConfig flows through ``Registry.from_world``
        onto the exact same engine path as a config-file run.  Any compose/validate error
        degrades to the scenario's default cast so Summon always yields a runnable show
        (and with no credentials the deterministic stub drives it, demo reproducible)."""
        from src.core.registry import Registry
        from src.ui.fishbowl.lab import collect_world_config

        def _num(key):
            v = knobs.get(key)
            return v if isinstance(v, (int, float)) else None

        def _dict(key):
            v = knobs.get(key)
            return v if isinstance(v, dict) else {}

        try:
            world = collect_world_config(
                scenario=name,
                premise=knobs.get("premise") or "",
                seed=knobs.get("seed") or "",
                cast_models=_dict("cast_models"),
                judge_policy=knobs.get("judge_policy") or "Majority Vote",
                judge_model=knobs.get("judge_model") or "",
                judge_strictness=knobs.get("judge_strictness")
                if isinstance(knobs.get("judge_strictness"), (int, float))
                else 50,
                tools=knobs.get("tools") if isinstance(knobs.get("tools"), list) else [],
                tokens=_num("tokens"),
                max_rounds=_num("max_rounds"),
                backend=knobs.get("backend")
                if isinstance(knobs.get("backend"), str) and knobs.get("backend")
                else "modal",
                cast_tools=_dict("cast_tools"),
                cast_personas=_dict("cast_personas"),
                cast_schedules=_dict("cast_schedules"),
                cast_roster=knobs.get("cast_roster") if isinstance(knobs.get("cast_roster"), list) else None,
                genesis=knobs.get("genesis") if isinstance(knobs.get("genesis"), str) else None,
                max_turns=_num("max_turns"),
                max_calls_per_turn=_num("max_calls_per_turn"),
                max_total_tokens=_num("max_total_tokens"),
                hourly_budget_usd=_num("hourly_budget_usd"),
            )
            return FishbowlSession(name, registry=Registry.from_world(world), tools=_tools)
        except Exception:
            return _new_session(name)  # bad compose → default cast; Summon never breaks

    # ── Summon: build a fresh session from the composed run, reset, jump to the Show ──
    def on_summon(
        scenario_value,
        seed_value,
        premise,
        cast_models,
        judge_model,
        judge_policy,
        judge_strictness,
        tools,
        backend,
        cast_tools,
        cast_personas,
        cast_schedules,
        cast_roster,
        genesis,
        max_turns,
        max_calls_per_turn,
        max_total_tokens,
        hourly_budget_usd,
        layout,
        mind_reader,
        session_id,
    ):
        title = _scenario_title(scenario_value)
        name = SCENARIOS.get(title, "")
        session = (
            _compose_session(
                name,
                premise=premise,
                seed=seed_value,
                cast_models=cast_models,
                judge_model=judge_model,
                judge_policy=judge_policy,
                judge_strictness=judge_strictness,
                tools=tools,
                backend=backend,
                cast_tools=cast_tools,
                cast_personas=cast_personas,
                cast_schedules=cast_schedules,
                cast_roster=cast_roster,
                genesis=genesis,
                max_turns=max_turns,
                max_calls_per_turn=max_calls_per_turn,
                max_total_tokens=max_total_tokens,
                hourly_budget_usd=hourly_budget_usd,
            )
            if name
            else None
        )
        if session is not None:
            session.reset((seed_value or "").strip(), session_id=(session_id or "").strip() or None)
            k = session.head
        else:
            k = 0
        out = _render_at(session, k, layout=layout, mind_reader=mind_reader)
        # Summon starts a fresh run: clear the stopped flag and the autoplay backstop.
        return (session, k, title, gr.update(selected="show"), *_pad_values(out, show_outs), False, 0)

    if summon_btn is not None:
        summon_inputs = [
            scenario_in if scenario_in is not None else scenario_state,
            seed_in if seed_in is not None else blank_state,  # empty seed when no widget
            premise_in if premise_in is not None else blank_state,
            cast_models_in if cast_models_in is not None else blank_state,
            judge_model_in if judge_model_in is not None else blank_state,
            judge_policy_in if judge_policy_in is not None else blank_state,
            judge_strictness_in if judge_strictness_in is not None else blank_state,
            tools_in if tools_in is not None else blank_state,
            backend_in if backend_in is not None else blank_state,
            cast_tools_in if cast_tools_in is not None else blank_state,
            cast_personas_in if cast_personas_in is not None else blank_state,
            cast_schedules_in if cast_schedules_in is not None else blank_state,
            cast_roster_in if cast_roster_in is not None else blank_state,
            genesis_in if genesis_in is not None else blank_state,
            max_turns_in if max_turns_in is not None else blank_state,
            max_calls_in if max_calls_in is not None else blank_state,
            max_tokens_in if max_tokens_in is not None else blank_state,
            hourly_budget_in if hourly_budget_in is not None else blank_state,
            layout_state,
            mind_reader_state,
            session_id_box if session_id_box is not None else blank_state,
        ]
        summon_btn.click(
            on_summon,
            inputs=summon_inputs,
            outputs=[session_state, k_state, scenario_state, tabs, *show_outs, stopped_state, tick_count_state],
        )

    # ── Scenario picked → re-seed the dependent Lab fields from the registry ─────
    # Without this, switching to a new world (e.g. the spy game) leaves the premise,
    # seed, cast table, and narrator showing the previous scenario — and Summon would
    # genesis with the wrong seed.  Refreshing them makes "compose a spy game" one click.
    # The cast picker re-seeds itself (it is a gr.render over the scenario + backend), as
    # does the Judge model picker (the Lab owns both, since they depend on the chosen
    # backend); we re-seed only the static, backend-independent fields here.
    _scenario_fields = [
        (_h(lab_handles, "premise"), "premise"),
        (seed_in, "seed"),
        (_h(lab_handles, "world"), "world"),
        (_h(lab_handles, "narrator"), "narrator"),
    ]
    _present_fields = [(handle, key) for handle, key in _scenario_fields if handle is not None]

    if scenario_in is not None and _present_fields:

        def on_scenario_change(scenario_value):
            cfg = _registry.scenarios.get(SCENARIOS.get(_scenario_title(scenario_value), ""))
            if cfg is None:
                return tuple(gr.update() for _ in _present_fields)
            # The seed is now an editable textbox (the preset dropdown that fills it is owned
            # by the Lab and reseeds itself); refresh its value, not its choices.
            updates = {
                # Premise is a dropdown-that-supports-text: refresh its single preset
                # (the new world's goal) and select it; a custom typed value still wins.
                "premise": gr.update(choices=[cfg.goal] if cfg.goal else [], value=cfg.goal),
                "seed": gr.update(value=cfg.default_seed),
                "world": gr.update(value=cfg.genesis_text or ""),
                "narrator": gr.update(value=scenario_voice(cfg.name)),
            }
            return tuple(updates[key] for _handle, key in _present_fields)

        scenario_in.change(
            on_scenario_change,
            inputs=[scenario_in],
            outputs=[handle for handle, _key in _present_fields],
        )

    # ── ⏭ / ▶ at head → step (generate) then render at the new head ─────────────
    # Returns (k, *show_outs, *halt-tail) — a tripped governor stops the timer and
    # paints the STOPPED banner instead of crashing the Gradio callback.
    def step_at_head(session, layout, mind_reader):
        if session is None:
            out = _render_at(None, 0, layout=layout, mind_reader=mind_reader)
            return (0, *_pad_values(out, show_outs), *_run_tail())
        try:
            session.step_one()  # ⏭ advances one agent so each utterance shows on its own
        except BudgetExceeded as exc:
            reason = getattr(exc, "reason", None) or str(exc)
            panes = _stopped_panes(session, session.head, layout=layout, mind_reader=mind_reader, reason=reason)
            return (session.head, *panes, *_halt_tail())
        k = session.head
        out = _render_at(session, k, layout=layout, mind_reader=mind_reader)
        return (k, *_pad_values(out, show_outs), *_run_tail())

    if step_btn is not None and show_outs:
        step_btn.click(
            step_at_head,
            inputs=[session_state, layout_state, mind_reader_state],
            outputs=[k_state, *show_outs, *_tail_outs],
        )

    # ── scrubber / ⏮ → pure prefix view (no stepping) ───────────────────────────
    def scrub_to(session, k, layout, mind_reader):
        kk = int(k or 0)
        out = _render_at(session, kk, layout=layout, mind_reader=mind_reader)
        return (kk, *_pad_values(out, show_outs))

    if scrubber is not None and show_outs:
        scrubber.change(
            scrub_to,
            inputs=[session_state, scrubber, layout_state, mind_reader_state],
            outputs=[k_state, *show_outs],
        )

    def to_start(session, layout, mind_reader):
        out = _render_at(session, 0, layout=layout, mind_reader=mind_reader)
        return (0, *_pad_values(out, show_outs))

    if back_btn is not None and show_outs:
        back_btn.click(
            to_start,
            inputs=[session_state, layout_state, mind_reader_state],
            outputs=[k_state, *show_outs],
        )

    # ── gr.Timer.tick → hybrid: advance k (replay) below head, else step ────────
    # Loop-safe: stops autoplay on a tripped governor, on a verdict at the head (the
    # show resolved), or after the governor-derived tick cap of consecutive generating
    # ticks (the hard backstop).  The cap tracks the scenario budget so a long show that
    # ends on a late Judge verdict is never cut off early.  Returns (k, *show_outs,
    # *halt-tail, tick_count).
    def on_tick(session, k, layout, mind_reader, tick_count):
        cap = session.autoplay_tick_cap if session is not None else _MAX_AUTO_TICKS
        new_k, new_ticks, stop_reason = advance_one_tick(session, k, tick_count, max_auto_ticks=cap)
        if stop_reason is not None:
            panes = _stopped_panes(session, new_k, layout=layout, mind_reader=mind_reader, reason=stop_reason)
            return (new_k, *panes, *_halt_tail(), new_ticks)
        out = _render_at(session, new_k, layout=layout, mind_reader=mind_reader)
        return (new_k, *_pad_values(out, show_outs), *_run_tail(), new_ticks)

    if timer is not None and show_outs:
        timer.tick(
            on_tick,
            inputs=[session_state, k_state, layout_state, mind_reader_state, tick_count_state],
            outputs=[k_state, *show_outs, *_tail_outs, tick_count_state],
        )

    # ── speed radio → timer interval; play/pause → timer.active ──────────────────
    if speed_radio is not None and timer is not None:

        def on_speed(speed):
            return gr.update(value=SPEEDS.get(speed, SPEEDS["1×"]))

        speed_radio.change(on_speed, inputs=[speed_radio], outputs=[timer])

    if play_btn is not None and timer is not None:
        # A tiny state tracks timer activity so the same button toggles play/pause.
        play_active = gr.State(False)

        def on_play(active):
            # Toggle play/pause; each fresh ▶ resets the autoplay backstop counter so the
            # next run gets a full _MAX_AUTO_TICKS budget rather than inheriting the old one.
            new = not bool(active)
            return new, gr.update(active=new), 0

        play_btn.click(on_play, inputs=[play_active], outputs=[play_active, timer, tick_count_state])

    # ── layout radio + "Read their minds" → re-render the stage HTML ─────────────
    if layout_radio is not None and show_outs:

        def on_layout(value, session, k, mind_reader):
            out = _render_at(session, int(k or 0), layout=value, mind_reader=mind_reader)
            return (value, *_pad_values(out, show_outs))

        layout_radio.change(
            on_layout,
            inputs=[layout_radio, session_state, k_state, mind_reader_state],
            outputs=[layout_state, *show_outs],
        )

    if mind_toggle is not None and show_outs:

        def on_minds(value, session, k, layout):
            out = _render_at(session, int(k or 0), layout=layout, mind_reader=bool(value))
            return (bool(value), *_pad_values(out, show_outs))

        mind_toggle.change(
            on_minds,
            inputs=[mind_toggle, session_state, k_state, layout_state],
            outputs=[mind_reader_state, *show_outs],
        )

    # ── poke buttons / poke_send → inject then render at the new head ────────────
    # Injecting steps the conductor, so a poke can trip the governor too — wrap it.
    def _poke_after(session, text, label_value, layout, mind_reader):
        if session is None:
            out = _render_at(None, 0, layout=layout, mind_reader=mind_reader)
            return (0, *_pad_values(out, show_outs), *_run_tail())
        try:
            session.inject((text or ""), label=label_value)
        except BudgetExceeded as exc:
            reason = getattr(exc, "reason", None) or str(exc)
            panes = _stopped_panes(session, session.head, layout=layout, mind_reader=mind_reader, reason=reason)
            return (session.head, *panes, *_halt_tail())
        k = session.head
        out = _render_at(session, k, layout=layout, mind_reader=mind_reader)
        return (k, *_pad_values(out, show_outs), *_run_tail())

    if poke_send is not None and poke_text is not None and show_outs:
        # Free-text poke: the textbox supplies the text; a neutral label.
        def on_poke_send(session, text, layout, mind_reader):
            return _poke_after(session, text, None, layout, mind_reader)

        poke_send.click(
            on_poke_send,
            inputs=[session_state, poke_text, layout_state, mind_reader_state],
            outputs=[k_state, *show_outs, *_tail_outs],
        )

    for btn in poke_buttons:
        if btn is None or not show_outs:
            continue
        # Preset poke buttons: the button's own label is both the disturbance text
        # and its label; capture it per-button so each button injects its own.
        label = str(getattr(btn, "value", None) or "DISTURBANCE")

        def make_preset(label_value: str):
            def _on_preset(session, layout, mind_reader):
                return _poke_after(session, label_value, label_value, layout, mind_reader)

            return _on_preset

        btn.click(
            make_preset(label),
            inputs=[session_state, layout_state, mind_reader_state],
            outputs=[k_state, *show_outs, *_tail_outs],
        )


# ── small output helper (keep emitted values aligned to present panes) ──────────


def _pad_values(values, show_outs: list) -> tuple:
    """Trim a (stage, feed, meters, verdict) tuple to the present panes' count."""
    return tuple(values[: len(show_outs)])


# ── Archive drawer ("my past sessions" → read-only replay) ──────────────────────

# Resolve (or mint) a per-browser session id from localStorage on page load.  Kept in
# JS so the id is the user's own and survives reloads — Python only reads the carrier.
_SESSION_ID_JS = """
() => {
  try {
    let id = localStorage.getItem('fishbowl_session_id');
    if (!id) {
      id = (window.crypto && crypto.randomUUID)
        ? crypto.randomUUID()
        : 'sess-' + Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem('fishbowl_session_id', id);
    }
    return id;
  } catch (e) {
    return 'sess-' + Math.random().toString(36).slice(2);
  }
}
"""


def _title_for(value) -> str:
    """Resolve a scenario title *or* internal name to a display-title key."""
    if value in SCENARIOS:
        return value
    for title, name in SCENARIOS.items():
        if value == name:
            return title
    return _DEFAULT_TITLE


def _show_outs(show_handles: dict) -> list:
    """The present Show panes (stage, feed, meters, verdict), in render order."""
    stage = _h(show_handles, "stage", "stage_html", "constellation")
    feed = _h(show_handles, "feed", "feed_html")
    meters = _h(show_handles, "meters", "meters_html")
    verdict = _h(show_handles, "verdict", "verdict_html")
    return [c for c in (stage, feed, meters, verdict) if c is not None]


def _archive_empty_html() -> str:
    """The drawer's empty state — no past runs for this world yet."""
    return _fishbowl(
        '<div class="archive-empty">'
        '<div class="eyebrow">&#10227; No past sessions</div>'
        '<div class="ae-body">Summon the bowl, and your runs in this world '
        "will gather here — yours alone, replayable any time.</div>"
        "</div>",
        role="fb-archive",
    )


def _build_archive_drawer(*, scenario_handle, session_id_box, refs: dict, tabs, states: dict) -> None:
    """The Lab's "Past sessions" accordion: clickable phosphor cards → read-only replay.

    A ``gr.render`` keyed on (scenario, session id) lists *this user's* runs for the
    *current* world via :func:`list_runs`; clicking a card loads that run with
    :func:`load_replay` and jumps to the Show.  The list re-renders on world change,
    when the session id resolves from localStorage, and on the manual refresh.
    """
    if scenario_handle is None:  # no scenario picker → nothing to scope a list to
        return

    with gr.Accordion("⟲ Past sessions · this world", open=False, elem_classes=["archive-drawer"]):
        refresh = gr.Button("⟳ refresh", size="sm", elem_classes=["archive-refresh"], scale=0)

        @gr.render(
            inputs=[scenario_handle, session_id_box],
            triggers=[scenario_handle.change, session_id_box.change, refresh.click],
        )
        def _render_archive(scenario_value, session_id):
            name = SCENARIOS.get(_title_for(scenario_value), "")
            runs = list_runs(name, session_id)
            if not runs:
                gr.HTML(_archive_empty_html())
                return

            show_outs = _show_outs(refs.get("show_handles") or {})
            n_out = 6 + len(show_outs)  # session, k, scenario, tabs, *panes, stopped, ticks

            def _loader(run_id: str):
                def _load(layout, mind_reader):
                    session = load_replay(run_id, registry=_registry, tools=_tools)
                    if session is None:
                        return tuple(gr.update() for _ in range(n_out))
                    k = session.head  # land on the full discussion; scrub/▶ replays it
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

            for summary in runs:
                card = gr.Button(run_card_label(summary), elem_classes=["archive-card"])
                card.click(
                    _loader(summary.run_id),
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


# ── dev server port (ported from the original root app.py) ──────────────────────


def on_spaces() -> bool:
    """True when running inside a Hugging Face Space (incl. ZeroGPU).

    HF injects ``SPACE_ID`` into every Space container; nothing else sets it."""
    return bool(os.getenv("SPACE_ID"))


def dev_server_port() -> int:
    # The platform's reverse proxy only reaches the app on the port it forwards to, so
    # an explicit ``GRADIO_SERVER_PORT``/``PORT`` from the environment MUST win over the
    # dev-range scanner below.  On HF Spaces the proxy forwards to 7860 but only sets
    # ``GRADIO_SERVER_NAME`` (not the port) — without this, the scanner picks 7960 and
    # the Space builds "successfully" yet is permanently unreachable (the bug this fixes).
    configured = os.getenv("GRADIO_SERVER_PORT") or os.getenv("PORT")
    if configured:
        return int(configured)
    if on_spaces():
        return 7860  # HF Spaces' fixed forward port; never scan a range it can't see.
    for port in range(7960, 8060):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free development port found in range 7960-8059.")


# ── module-level app + launch ────────────────────────────────────────────────────

demo = build_app()


def _launch_kwargs() -> dict:
    """Theme/css/head applied at launch() time (Gradio 6), when available."""
    kwargs: dict = {"css": load_css()}
    if FISHBOWL_HEAD:
        kwargs["head"] = FISHBOWL_HEAD
    theme = FishbowlTheme() if callable(FishbowlTheme) else FishbowlTheme
    if theme is not None:
        kwargs["theme"] = theme
    return kwargs


def launch(**overrides):
    """Launch the FISHBOWL theater with theme/css/head + a free dev port.

    The single entry point the root shim and ``uv run app.py`` call; keeps the
    no-API-key offline behaviour (the deterministic stub drives the cast)."""
    kwargs = {"server_port": dev_server_port(), **_launch_kwargs()}
    if on_spaces():
        # Bind every interface so HF's proxy can reach us (defensive — HF also sets
        # GRADIO_SERVER_NAME), and drop Gradio 6's SSR Node proxy: on ZeroGPU it spawns
        # a second (Node) process and a +1 internal port purely to pre-render the first
        # paint.  This live, stream-driven theater gets ~nothing from SSR, so disabling
        # it trims cold-start time and memory on the resource-capped Space.
        kwargs.setdefault("server_name", "0.0.0.0")
        kwargs.setdefault("ssr_mode", False)
    kwargs.update(overrides)
    return demo.launch(**kwargs)


if __name__ == "__main__":
    launch()
