from __future__ import annotations

import os
import socket

import gradio as gr

from src.core.conductor import Conductor
from src.core.registry import default_registry
from src.tools.builtins import default_tool_registry
from src.ui.render import render_config, render_event_log, render_stage, render_stats

# ── scenario registry (assembled from config/, not hardcoded) ───────────────────

_registry = default_registry()
_tools = default_tool_registry()
_PROFILE_MODELS = _registry.build_router().describe()

# Preferred display order, then any other scenarios dropped into config/.
_PREFERRED = ["thousand-token-wood", "mystery-roots", "oracle-grove"]
_names = [n for n in _PREFERRED if n in _registry.scenarios] + [
    n for n in sorted(_registry.scenarios) if n not in _PREFERRED
]

# display title -> internal scenario name
SCENARIOS: dict[str, str] = {(_registry.scenarios[n].title or n): n for n in _names}

_conductors: dict[str, Conductor] = {
    title: Conductor(
        _registry.build_scenario(name, tools=_tools),
        governor=_registry.governor_for(name),
    )
    for title, name in SCENARIOS.items()
}

# ── CSS ───────────────────────────────────────────────────────────────────────

APP_CSS = """
:root {
  --bg: #0e1209;
  --surface: #141a0f;
  --border: #2e3d25;
  --text: #e8e2cc;
  --muted: #8a9c7a;
  --accent: #6db56d;
  --accent2: #c9a84c;
  --danger: #c96b6b;
}
body { background: var(--bg); color: var(--text); }
.gradio-container { max-width: 1200px !important; font-family: 'Georgia', serif; }
footer { display: none !important; }

/* Header */
.wood-header h1 {
  font-size: 2rem;
  color: var(--accent);
  letter-spacing: .04em;
  margin-bottom: 2px;
}
.wood-header p { color: var(--muted); margin-top: 0; font-style: italic; }

/* Stage */
#stage {
  border: 1px solid var(--border);
  background: linear-gradient(180deg, #172017 0%, var(--surface) 100%);
  color: var(--text);
  padding: 20px 24px;
  border-radius: 10px;
  min-height: 380px;
  font-size: 0.97rem;
  line-height: 1.7;
}
#stage h2 { color: var(--accent); font-size: 1.1rem; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
#stage h3 { color: var(--accent2); font-size: 0.95rem; margin-top: 16px; margin-bottom: 6px; }

/* Ledger + stats */
#events textarea, #stats textarea {
  font-family: ui-monospace, 'Cascadia Code', 'Fira Mono', monospace;
  font-size: 11px;
  background: var(--surface);
  color: var(--muted);
  border-color: var(--border);
}

/* Buttons */
button.primary { background: var(--accent) !important; color: var(--bg) !important; font-weight: 700; }
button.secondary { border-color: var(--border) !important; color: var(--muted) !important; }

/* Scenario selector */
.scenario-selector label { color: var(--accent2) !important; }

/* Seed input */
#seed-box textarea { font-style: italic; }

/* Inject row */
#inject-box textarea { border-color: var(--accent2) !important; }

/* Status pill */
.status-pill {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 0.75rem;
  font-family: monospace;
  background: var(--surface);
  border: 1px solid var(--border);
  color: var(--muted);
}
"""

# ── helpers ───────────────────────────────────────────────────────────────────


def _conductor(scenario_name: str) -> Conductor:
    return _conductors[scenario_name]


def _outputs(c: Conductor):
    return (
        render_stage(c.projection),
        render_event_log(c.ledger.events),
        render_stats(c.ledger.events, c.governor),
        render_config(c.scenario, _PROFILE_MODELS),
    )


def start(scenario_name: str, seed: str):
    c = _conductor(scenario_name)
    c.reset(seed.strip() or c.scenario.default_seed)
    return _outputs(c)


def step(scenario_name: str):
    c = _conductor(scenario_name)
    c.step()
    return _outputs(c)


def inject(scenario_name: str, user_event: str):
    c = _conductor(scenario_name)
    if user_event.strip():
        c.inject_user_event(user_event.strip())
    c.step()
    return _outputs(c)


def change_scenario(scenario_name: str):
    c = _conductor(scenario_name)
    seeds = c.scenario.example_seeds
    choices = [(s, s) for s in seeds]
    default = seeds[0] if seeds else c.scenario.default_seed
    return (
        gr.update(choices=choices, value=default),
        render_config(c.scenario, _PROFILE_MODELS),
    )


# ── layout ────────────────────────────────────────────────────────────────────

with gr.Blocks(title="Multi-Agent Land · Thousand Token Wood") as demo:  # css passed to launch() (Gradio 6)
    gr.Markdown(
        """
        # Multi-Agent Land
        *Tiny specialist agents share a ledger and build a living world — turn by turn.*
        """,
        elem_classes=["wood-header"],
    )

    with gr.Row():
        scenario_select = gr.Dropdown(
            choices=list(SCENARIOS.keys()),
            value=list(SCENARIOS.keys())[0],
            label="Scenario",
            elem_classes=["scenario-selector"],
            scale=1,
        )

    with gr.Row():
        _first_title = list(SCENARIOS.keys())[0]
        seed_examples = _conductors[_first_title].scenario.example_seeds
        seed = gr.Dropdown(
            choices=[(s, s) for s in seed_examples],
            value=seed_examples[0],
            label="World seed",
            allow_custom_value=True,
            elem_id="seed-box",
            scale=4,
        )
        with gr.Column(scale=1, min_width=160):
            start_btn = gr.Button("▶ Start", variant="primary")
            step_btn = gr.Button("⏭ Advance one turn", variant="secondary")

    stage = gr.Markdown(
        value="> Select a scenario and press **Start** to raise the curtain.",
        elem_id="stage",
    )

    with gr.Row():
        user_event = gr.Textbox(
            label="Drop something into the world",
            placeholder="Example: A lantern starts whispering recipes.",
            lines=2,
            elem_id="inject-box",
            scale=4,
        )
        inject_btn = gr.Button("💬 Inject & advance", scale=1, min_width=160)

    with gr.Row():
        events_box = gr.Textbox(label="Event ledger (append-only)", lines=18, elem_id="events")
        stats_box = gr.Textbox(label="Run stats", lines=18, elem_id="stats")

    with gr.Accordion("⚙ Configuration — live, from config/ (YAML, not code)", open=False):
        config_box = gr.Markdown(
            value=render_config(_conductors[_first_title].scenario, _PROFILE_MODELS),
            elem_id="config",
        )

    # ── wiring ────────────────────────────────────────────────────────────────
    _outs = [stage, events_box, stats_box, config_box]
    scenario_select.change(change_scenario, inputs=[scenario_select], outputs=[seed, config_box])

    start_btn.click(start, inputs=[scenario_select, seed], outputs=_outs)
    step_btn.click(step, inputs=[scenario_select], outputs=_outs)
    inject_btn.click(inject, inputs=[scenario_select, user_event], outputs=_outs)


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


if __name__ == "__main__":
    demo.launch(css=APP_CSS, server_port=dev_server_port())
