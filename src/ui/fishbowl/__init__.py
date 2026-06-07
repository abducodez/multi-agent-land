"""Fishbowl UI presenter — turns engine events into the design's view-model.

Pure and transport-agnostic (no Gradio import here): the same snapshot feeds the
``gr.HTML`` stage now and a future ``gr.Server`` JSON endpoint (ADR-0021).  This
package depends only on the engine's public read surface — ``ledger.events``,
``rebuild_stage``, ``governor.stats``, agent manifests — and the engine never imports
it, so ``tests/test_modularity.py`` and the four contracts are untouched.

Layers:
  * ``cast_state``  — ``derive_cast_state`` : per-agent {said, thought, mood} ledger view (G1)
  * ``adapter``     — engine vocabulary → the design's say/narrate/poke/verdict + hue/tier/voice
  * ``view_model``  — ``view_model_at`` : a JSON-serialisable snapshot at any scrubbed step k

The Gradio shell (``build_app`` / ``demo``) also lives in this package (``app`` module),
but is exposed **lazily** via :pep:`562` ``__getattr__`` so that importing the package
stays Gradio-free — the pure presenter above must remain importable without Gradio for
``tests/test_modularity.py`` and the JSON-endpoint path.
"""

from typing import TYPE_CHECKING

from src.ui.fishbowl.cast_state import CastMemberState, derive_cast_state
from src.ui.fishbowl.view_model import view_model_at

if TYPE_CHECKING:  # for type-checkers only; no runtime Gradio import
    from src.ui.fishbowl.app import build_app, demo

__all__ = ["CastMemberState", "derive_cast_state", "view_model_at", "build_app", "demo"]


def __getattr__(name: str):
    """Lazily expose the Gradio shell so ``import src.ui.fishbowl`` stays Gradio-free."""
    if name in ("build_app", "demo"):
        from src.ui.fishbowl import app as _app

        return getattr(_app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
