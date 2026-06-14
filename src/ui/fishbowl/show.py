"""The Show tab — the component skeleton for the live theater (Unit 7).

Mirrors the ``ui/raw/show.jsx`` prototype: a ShowBar (back-to-lab, mind-reader
toggle, layout selector), three HTML projection panels (stage / feed / meters)
plus a verdict banner, a hybrid transport (⏮/▶/⏭ + scrubber + speed segmented
control + autoplay timer), and a poke strip (preset world disturbances + a
free-text drop).

This module owns the Gradio component **tree** only.  It builds components inside
the caller's ``gr.Blocks`` context and returns a dict of handles.  It deliberately
does **not** wire callbacks, compose HTML, or import the render/session modules —
the app shell (Unit 9) owns all wiring.  ``elem_id``/``elem_classes`` follow the
CSS vocabulary from the prototype (``transport``, ``scrub``, ``seg``,
``poke-strip``, …) so Unit 1's CSS can style the tree.
"""

from __future__ import annotations

import gradio as gr

# Preset world disturbances surfaced as poke buttons.  Mirrors the design's
# "Poke the world" strip; the app shell wires each to a ledger poke event.
POKE_PRESETS: list[str] = ["GUST OF WIND", "CALL THE VOTE", "LIGHTS FLICKER"]


def build_show() -> dict[str, object]:
    """Build the Show tab component tree and return handles for wiring.

    Must be called inside an active ``gr.Blocks`` context (the app shell calls it
    inside ``gr.Tab("The Show")``); components register with that Blocks
    automatically.  Returns every handle so Unit 9 can attach callbacks.
    """
    handles: dict[str, object] = {}

    with gr.Column(elem_id="show", elem_classes=["show"]):
        # ---- SHOW BAR : PLAY/PAUSE · RESTART · mind-reader · layout -------
        # The Play/Pause control rides up here at the top of the Show, front-and-centre:
        # it is the one thing a visitor needs.  Its label flips "▶ Play"⇄"❚❚ Pause"; the
        # app shell owns the toggle, auto-starts it on Summon, and begins the conversation
        # the instant it's pressed.  Restart wipes the run and begins a fresh context.
        with gr.Row(elem_classes=["showbar"]):
            handles["play_btn"] = gr.Button(
                "▶ Play",
                elem_classes=["icon-btn", "play", "play-hero"],
                scale=0,
            )
            handles["restart_btn"] = gr.Button(
                "↺ Restart",
                elem_classes=["icon-btn", "restart-btn"],
                scale=0,
            )
            handles["mind_reader"] = gr.Checkbox(
                value=False,
                label="Read their minds",
                elem_classes=["mindreader"],
            )
            handles["layout"] = gr.Radio(
                ["constellation", "feed", "split"],
                value="constellation",
                label="Layout",
                elem_classes=["seg", "layout-seg"],
            )

        # ---- BODY : the projection panels (filled by Unit 9's callbacks) --
        # The stage gets the lion's share of the width (≈ the design's `1fr 384px`
        # split) so the MindCards have room to breathe; the narrator rail is fixed.
        with gr.Row(elem_classes=["show-body"]):
            with gr.Column(scale=3, min_width=420, elem_classes=["stage-wrap"]):
                handles["stage_html"] = gr.HTML(
                    value="<div class='stage' aria-label='stage'></div>",
                    elem_id="stage-html",
                    elem_classes=["stage-panel"],
                )
            with gr.Column(scale=2, min_width=300, elem_classes=["rail"]):
                handles["feed_html"] = gr.HTML(
                    value="<div class='feed scroll' aria-label='narrator feed'></div>",
                    elem_id="feed-html",
                    elem_classes=["feed-panel"],
                )

                # ---- FROM THE RAFTERS : the critic's navigable illustrated + spoken gallery,
                # sitting UNDER the chat. Native gr.Image / gr.Audio (the /file= route is dead
                # in Gradio 5+; native components emit the correct /gradio_api/file= URL and
                # auto-allow the file). Driven by a cards gr.State (the app shell polls the
                # ledger into it) + a view index, rendered via @gr.render so it only (re)builds
                # — and only reloads the audio — when a NEW beat arrives or the visitor
                # navigates, never on the high-frequency autoplay re-render (which previously
                # reset the audio every tick → "voice stuck loading"). ‹ Prev / Next › walk the
                # critic's whole run of beats; the newest reveals itself the moment it lands.
                rafters_cards = gr.State([])
                rafters_idx = gr.State(0)
                handles["rafters_cards"] = rafters_cards
                handles["rafters_idx"] = rafters_idx
                handles["rafters_timer"] = gr.Timer(value=1.2)

                # Every component carries a STABLE ``key`` so @gr.render treats it as the
                # same component across re-renders (Gradio docs) and updates it in place
                # instead of destroying + recreating it. That is the real fix for the audio
                # "AbortError: signal is aborted without reason": without a key the <gr.Audio>
                # was unmounted on every new beat, aborting its in-flight loadAudio(); keyed,
                # the player persists and just swaps its source. ``preserved_by_key=[]`` on the
                # value-bearing components forces the new card's value to apply each render
                # (they are display-only, never user-edited, so nothing needs preserving).
                @gr.render(inputs=[rafters_cards, rafters_idx])
                def _rafters_gallery(cards, idx):
                    cards = cards or []
                    if not cards:
                        return  # nothing until the heckler's first illustrated/voiced beat
                    n = len(cards)
                    i = max(0, min(int(idx or 0), n - 1))
                    card = cards[i]
                    with gr.Column(elem_id="rafters-box", elem_classes=["rafters-box"], key="rafters-box"):
                        gr.HTML(
                            '<div class="rafters-head"><span class="rafters-quill">&#9998;</span>'
                            f' FROM THE RAFTERS<span class="rafters-count">{i + 1}&#8202;/&#8202;{n}</span></div>',
                            key="rafters-head",
                            preserved_by_key=[],
                        )
                        if card.get("image"):
                            gr.Image(
                                card["image"],
                                interactive=False,
                                show_label=False,
                                container=False,
                                elem_classes=["rafters-img"],
                                key="rafters-img",
                                preserved_by_key=[],
                            )
                        if card.get("caption"):
                            gr.HTML(
                                f'<p class="rafters-cap">{card["caption"]}</p>',
                                key="rafters-cap",
                                preserved_by_key=[],
                            )
                        if card.get("audio"):
                            # autoplay: the critic's line speaks the moment its card appears.
                            gr.Audio(
                                card["audio"],
                                interactive=False,
                                show_label=False,
                                container=False,
                                autoplay=True,
                                elem_classes=["rafters-audio"],
                                key="rafters-audio",
                                preserved_by_key=[],
                            )
                        with gr.Row(elem_classes=["rafters-nav"], key="rafters-nav"):
                            prev_btn = gr.Button(
                                "‹ Prev",
                                elem_classes=["rafters-navbtn"],
                                scale=1,
                                interactive=i > 0,
                                key="rafters-prev",
                            )
                            next_btn = gr.Button(
                                "Next ›",
                                elem_classes=["rafters-navbtn"],
                                scale=1,
                                interactive=i < n - 1,
                                key="rafters-next",
                            )
                        prev_btn.click(lambda i=i: max(0, i - 1), outputs=rafters_idx)
                        next_btn.click(lambda i=i, n=n: min(n - 1, i + 1), outputs=rafters_idx)

                handles["meters_html"] = gr.HTML(
                    value="<div class='meters panel' aria-label='meters'></div>",
                    elem_id="meters-html",
                    elem_classes=["meters-panel"],
                )

        # ---- VERDICT BANNER (filled by Unit 9 when a verdict lands) -------
        handles["verdict_html"] = gr.HTML(
            value="",
            elem_id="verdict-html",
            elem_classes=["verdict-banner"],
        )

        # ---- POKE STRIP : preset disturbances + free-text drop ------------
        with gr.Column(elem_classes=["poke-strip"]):
            poke_btns: list[gr.Button] = []
            with gr.Row(elem_classes=["poke-btns"]):
                for label in POKE_PRESETS:
                    poke_btns.append(gr.Button(label, elem_classes=["poke-b"], scale=0))
            handles["poke_btns"] = poke_btns
            with gr.Row(elem_classes=["poke-drop"]):
                handles["poke_text"] = gr.Textbox(
                    label="Drop something into the world",
                    placeholder="a sudden hush falls over the clearing…",
                    elem_classes=["poke-input"],
                    scale=4,
                )
                handles["poke_send"] = gr.Button(
                    "Drop it",
                    elem_classes=["poke-b", "poke-send"],
                    scale=0,
                )

        # ---- AUTOPLAY CLOCK : app shell toggles ``active`` + interval -----
        # Play/Pause + Restart live in the showbar; the manual "start judging" and speed
        # controls are gone — the judge is brought on automatically at the end of a run,
        # and the clock runs at a single comfortable cadence.
        handles["timer"] = gr.Timer(value=1.9, active=False)

    return handles


if __name__ == "__main__":
    import os

    with gr.Blocks(title="Fishbowl · The Show (skeleton)") as demo:
        build_show()

    demo.launch(
        server_name="127.0.0.1",
        server_port=int(os.environ.get("GRADIO_SERVER_PORT", "7981")),
    )
