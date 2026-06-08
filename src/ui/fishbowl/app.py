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
from src.core.ledger_factory import make_ledger
from src.core.registry import default_registry
from src.tools.builtins import default_tool_registry
from src.ui.fishbowl.adapter import scenario_voice
from src.ui.fishbowl.view_model import view_model_at

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

        def reset(self, seed: str) -> None:
            self.conductor.reset(seed or self.conductor.scenario.default_seed)

        def step(self) -> None:
            self.conductor.step()

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

_PREFERRED = ["thousand-token-wood", "the-steeped", "mystery-roots", "oracle-grove"]
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


# ── CRT theater chrome ──────────────────────────────────────────────────────────

_TOPBAR_HTML = """
<div class="fishbowl">
  <div class="topbar">
    <div class="brand">
      <span class="logo">&#9673; FISHBOWL</span>
      <span class="sub">a fishbowl of minds you can read</span>
    </div>
    <div class="topbar-status">
      <span class="chip live"><span class="live-dot"></span>OFFLINE-FIRST</span>
      <span class="topbar-tag eyebrow">small minds &middot; one ledger &middot; &le; 32B</span>
    </div>
  </div>
</div>
"""

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

    # Show transport controls.  Lookups list every name a leaf unit might use; the
    # shipped show.py keys (step_slider / fwd_btn / back_btn / poke_btns) are included
    # so the scrubber, ⏭/⏮ transport, and preset poke buttons are actually wired.
    timer = _h(show_handles, "timer")
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

    def _scenario_title(value) -> str:
        """Resolve a Lab scenario selection (title or internal name) to a title key."""
        if value in SCENARIOS:
            return value
        for title, name in SCENARIOS.items():
            if value == name:
                return title
        return _DEFAULT_TITLE

    # Scrubber sync: the step slider is a *timeline*, so its maximum must track the
    # ledger head as the run grows (it ships fixed at 0..1).  Handlers that move the
    # head append a slider update via these helpers; the scrubber's own change handler
    # does not (it is the input, not an output, so there is no feedback loop).
    scrub_outs = [scrubber] if scrubber is not None else []

    def _scrub(values: tuple, session, k) -> tuple:
        if scrubber is None:
            return values
        head = session.head if session is not None else 0
        return (*values, gr.update(maximum=max(1, head), value=int(k)))

    # ── Summon: build a fresh session, reset, jump to the Show, render head ──────
    def on_summon(scenario_value, seed_value, layout, mind_reader):
        title = _scenario_title(scenario_value)
        name = SCENARIOS.get(title, "")
        session = _new_session(name) if name else None
        if session is not None:
            session.reset((seed_value or "").strip())
            k = session.head
        else:
            k = 0
        out = _render_at(session, k, layout=layout, mind_reader=mind_reader)
        return _scrub((session, k, title, gr.update(selected="show"), *_pad_values(out, show_outs)), session, k)

    if summon_btn is not None:
        summon_inputs = [
            scenario_in if scenario_in is not None else scenario_state,
            seed_in if seed_in is not None else blank_state,  # empty seed when no widget
            layout_state,
            mind_reader_state,
        ]
        summon_btn.click(
            on_summon,
            inputs=summon_inputs,
            outputs=[session_state, k_state, scenario_state, tabs, *show_outs, *scrub_outs],
        )

    # ── Scenario picked → re-seed the dependent Lab fields from the registry ─────
    # Without this, switching to a new world (e.g. the spy game) leaves the premise,
    # seed, cast table, and narrator showing the previous scenario — and Summon would
    # genesis with the wrong seed.  Refreshing them makes "compose a spy game" one click.
    _scenario_fields = [
        (_h(lab_handles, "premise"), "premise"),
        (seed_in, "seed"),
        (_h(lab_handles, "world"), "world"),
        (_h(lab_handles, "cast"), "cast"),
        (_h(lab_handles, "narrator"), "narrator"),
    ]
    _present_fields = [(handle, key) for handle, key in _scenario_fields if handle is not None]

    if scenario_in is not None and _present_fields:

        def on_scenario_change(scenario_value):
            from src.ui.fishbowl.lab import _cast_rows_for

            cfg = _registry.scenarios.get(SCENARIOS.get(_scenario_title(scenario_value), ""))
            if cfg is None:
                return tuple(gr.update() for _ in _present_fields)
            seeds = list(cfg.example_seeds) or [cfg.default_seed]
            updates = {
                "premise": gr.update(value=cfg.goal),
                "seed": gr.update(choices=seeds, value=cfg.default_seed),
                "world": gr.update(value=cfg.genesis_text or ""),
                "cast": gr.update(value=_cast_rows_for(cfg)),
                "narrator": gr.update(value=scenario_voice(cfg.name)),
            }
            return tuple(updates[key] for _handle, key in _present_fields)

        scenario_in.change(
            on_scenario_change,
            inputs=[scenario_in],
            outputs=[handle for handle, _key in _present_fields],
        )

    # ── ⏭ / ▶ at head → step (generate) then render at the new head ─────────────
    def step_at_head(session, layout, mind_reader):
        if session is not None:
            session.step()
            k = session.head
        else:
            k = 0
        out = _render_at(session, k, layout=layout, mind_reader=mind_reader)
        return _scrub((k, *_pad_values(out, show_outs)), session, k)

    if step_btn is not None and show_outs:
        step_btn.click(
            step_at_head,
            inputs=[session_state, layout_state, mind_reader_state],
            outputs=[k_state, *show_outs, *scrub_outs],
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
        return _scrub((0, *_pad_values(out, show_outs)), session, 0)

    if back_btn is not None and show_outs:
        back_btn.click(
            to_start,
            inputs=[session_state, layout_state, mind_reader_state],
            outputs=[k_state, *show_outs, *scrub_outs],
        )

    # ── gr.Timer.tick → hybrid: advance k (replay) below head, else step ────────
    def on_tick(session, k, layout, mind_reader):
        k = int(k or 0)
        if session is None:
            out = _render_at(None, 0, layout=layout, mind_reader=mind_reader)
            return _scrub((0, *_pad_values(out, show_outs)), None, 0)
        if k < session.head:
            k += 1  # replay forward through the existing prefix
        else:
            session.step()  # at the head → generate
            k = session.head
        out = _render_at(session, k, layout=layout, mind_reader=mind_reader)
        return _scrub((k, *_pad_values(out, show_outs)), session, k)

    if timer is not None and show_outs:
        timer.tick(
            on_tick,
            inputs=[session_state, k_state, layout_state, mind_reader_state],
            outputs=[k_state, *show_outs, *scrub_outs],
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
            new = not bool(active)
            return new, gr.update(active=new)

        play_btn.click(on_play, inputs=[play_active], outputs=[play_active, timer])

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
    def _poke_after(session, text, label_value, layout, mind_reader):
        if session is not None:
            session.inject((text or ""), label=label_value)
            k = session.head
        else:
            k = 0
        out = _render_at(session, k, layout=layout, mind_reader=mind_reader)
        return _scrub((k, *_pad_values(out, show_outs)), session, k)

    if poke_send is not None and poke_text is not None and show_outs:
        # Free-text poke: the textbox supplies the text; a neutral label.
        def on_poke_send(session, text, layout, mind_reader):
            return _poke_after(session, text, None, layout, mind_reader)

        poke_send.click(
            on_poke_send,
            inputs=[session_state, poke_text, layout_state, mind_reader_state],
            outputs=[k_state, *show_outs, *scrub_outs],
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
            outputs=[k_state, *show_outs, *scrub_outs],
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
