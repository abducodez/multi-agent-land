"""Root entry point — a thin shim over the FISHBOWL Gradio shell.

The app itself lives in ``src.ui.fishbowl.app`` (Unit 9).  This shim keeps ``uv run
app.py`` working and preserves the no-API-key / offline behaviour: the deterministic
stub drives the cast so the demo is reproducible on stage with no credentials.
"""

from __future__ import annotations

from src.ui.fishbowl.app import demo, launch

__all__ = ["demo", "launch"]

if __name__ == "__main__":
    launch()
