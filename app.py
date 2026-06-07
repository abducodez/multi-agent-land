from __future__ import annotations

import os
import socket

import gradio as gr

from src.core.conductor import Conductor
from src.scenarios.thousand_token_wood import build_scenario
from src.ui.render import render_event_log, render_stage, render_stats


scenario = build_scenario()
conductor = Conductor(scenario=scenario)

APP_CSS = """
body { background: #10130f; }
.gradio-container { max-width: 1180px !important; }
#stage {
  border: 1px solid #3e4a36;
  background: linear-gradient(180deg, #172017 0%, #11160f 100%);
  color: #f4f0df;
  padding: 18px;
  border-radius: 8px;
  min-height: 420px;
}
#events textarea, #stats textarea {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}
.wood-title h1 { margin-bottom: 0; }
.wood-title p { margin-top: 6px; color: #b9c3a7; }
"""


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


def start(seed: str):
    conductor.reset(seed.strip() or scenario.default_seed)
    return (
        render_stage(conductor.projection),
        render_event_log(conductor.ledger.events),
        render_stats(conductor.ledger.events),
    )


def step():
    conductor.step()
    return (
        render_stage(conductor.projection),
        render_event_log(conductor.ledger.events),
        render_stats(conductor.ledger.events),
    )


def inject(user_event: str):
    if user_event.strip():
        conductor.inject_user_event(user_event.strip())
    conductor.step()
    return (
        render_stage(conductor.projection),
        render_event_log(conductor.ledger.events),
        render_stats(conductor.ledger.events),
    )


with gr.Blocks(title="Multi-Agent Land") as demo:
    gr.Markdown(
        """
        # Multi-Agent Land
        Tiny agents wander a strange wood, writing an interactive story through an append-only ledger.
        """,
        elem_classes=["wood-title"],
    )

    with gr.Row():
        seed = gr.Textbox(
            label="World seed",
            value=scenario.default_seed,
            lines=3,
        )
        with gr.Column(scale=0):
            start_button = gr.Button("Start run", variant="primary")
            step_button = gr.Button("Advance one turn")

    stage = gr.Markdown(value="Start a run to raise the curtain.", elem_id="stage")

    with gr.Row():
        user_event = gr.Textbox(
            label="Drop something into the wood",
            placeholder="Example: A lantern starts whispering recipes.",
            lines=2,
        )
        inject_button = gr.Button("Inject and advance")

    with gr.Row():
        events = gr.Textbox(label="Ledger", lines=18, elem_id="events")
        stats = gr.Textbox(label="Run stats", lines=18, elem_id="stats")

    start_button.click(start, inputs=[seed], outputs=[stage, events, stats])
    step_button.click(step, outputs=[stage, events, stats])
    inject_button.click(inject, inputs=[user_event], outputs=[stage, events, stats])


if __name__ == "__main__":
    demo.launch(css=APP_CSS, server_port=dev_server_port())
