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
        # ---- SHOW BAR : back-to-lab · mind-reader · layout selector -------
        with gr.Row(elem_classes=["showbar"]):
            handles["back_to_lab"] = gr.Button(
                "← back to the Lab",
                elem_classes=["icon-btn", "back-to-lab"],
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
        with gr.Row(elem_classes=["show-body"]):
            handles["stage_html"] = gr.HTML(
                value="<div class='stage' aria-label='stage'></div>",
                elem_id="stage-html",
                elem_classes=["stage-panel"],
            )
            with gr.Column(elem_classes=["rail"]):
                handles["feed_html"] = gr.HTML(
                    value="<div class='feed scroll' aria-label='narrator feed'></div>",
                    elem_id="feed-html",
                    elem_classes=["feed-panel"],
                )
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

        # ---- TRANSPORT : ⏮/▶/⏭ · scrubber · speed · autoplay clock -------
        with gr.Row(elem_classes=["transport"]):
            with gr.Row(elem_classes=["tp-btns"]):
                handles["back_btn"] = gr.Button("⏮", elem_classes=["icon-btn"], scale=0)
                handles["play_btn"] = gr.Button("▶", elem_classes=["icon-btn", "play"], scale=0)
                handles["fwd_btn"] = gr.Button("⏭", elem_classes=["icon-btn"], scale=0)
            handles["step_slider"] = gr.Slider(
                minimum=0,
                maximum=1,
                value=0,
                step=1,
                label="step",
                elem_classes=["scrub"],
                scale=4,
            )
            handles["speed"] = gr.Radio(
                ["live", "1×", "fast"],
                value="1×",
                label="speed",
                elem_classes=["seg", "speed-seg"],
            )

        # ---- AUTOPLAY CLOCK : app shell toggles ``active`` + interval -----
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
