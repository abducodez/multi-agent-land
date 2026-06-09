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


# ── scenario registry (assembled from config/, not hardcoded) ───────────────────

_registry = default_registry()
_tools = default_tool_registry()

_PREFERRED = ["thousand-token-wood", "mystery-roots", "oracle-grove"]
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

        with gr.Tabs() as tabs:
            with gr.Tab("The Lab", id="lab"):
                lab_handles = build_lab()
            with gr.Tab("The Show", id="show"):
                show_handles = build_show()

        # CRT foreground layers (scanlines + vignette, above content, click-through).
        gr.HTML(_CRT_FG_HTML)

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
        )

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
    tokens_in = _h(lab_handles, "tokens")
    max_rounds_in = _h(lab_handles, "max_rounds")

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

        try:
            world = collect_world_config(
                scenario=name,
                premise=knobs.get("premise") or "",
                seed=knobs.get("seed") or "",
                cast_models=knobs.get("cast_models") if isinstance(knobs.get("cast_models"), dict) else {},
                judge_policy=knobs.get("judge_policy") or "Majority Vote",
                judge_model=knobs.get("judge_model") or "",
                judge_strictness=knobs.get("judge_strictness")
                if isinstance(knobs.get("judge_strictness"), (int, float))
                else 50,
                tools=knobs.get("tools") if isinstance(knobs.get("tools"), list) else [],
                tokens=knobs.get("tokens") if isinstance(knobs.get("tokens"), (int, float)) else None,
                max_rounds=knobs.get("max_rounds") if isinstance(knobs.get("max_rounds"), (int, float)) else None,
                backend=knobs.get("backend") if isinstance(knobs.get("backend"), str) and knobs.get("backend") else "modal",
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
        tokens,
        max_rounds,
        backend,
        layout,
        mind_reader,
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
                tokens=tokens,
                max_rounds=max_rounds,
                backend=backend,
            )
            if name
            else None
        )
        if session is not None:
            session.reset((seed_value or "").strip())
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
            tokens_in if tokens_in is not None else blank_state,
            max_rounds_in if max_rounds_in is not None else blank_state,
            backend_in if backend_in is not None else blank_state,
            layout_state,
            mind_reader_state,
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
            seeds = list(cfg.example_seeds) or [cfg.default_seed]
            updates = {
                "premise": gr.update(value=cfg.goal),
                "seed": gr.update(choices=seeds, value=cfg.default_seed),
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
    # show resolved), or after _MAX_AUTO_TICKS consecutive generating ticks (the hard
    # backstop the user asked for).  Returns (k, *show_outs, *halt-tail, tick_count).
    def on_tick(session, k, layout, mind_reader, tick_count):
        new_k, new_ticks, stop_reason = advance_one_tick(session, k, tick_count)
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


# ── dev server port (ported from the original root app.py) ──────────────────────


def dev_server_port() -> int:
    configured = os.getenv("GRADIO_SERVER_PORT")
    if configured:
        return int(configured)
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
    kwargs = {"server_port": dev_server_port(), **_launch_kwargs(), **overrides}
    return demo.launch(**kwargs)


if __name__ == "__main__":
    launch()
