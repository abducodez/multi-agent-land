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
"""

from src.ui.fishbowl.cast_state import CastMemberState, derive_cast_state
from src.ui.fishbowl.view_model import view_model_at

__all__ = ["CastMemberState", "derive_cast_state", "view_model_at"]
