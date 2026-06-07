"""Fishbowl HTML-string renderers (pure; no Gradio import).

These turn a ``view_model_at(...)`` snapshot into the design's markup so any transport
(``gr.HTML`` now, ``gr.Server`` later) can paint the Show.  Unit 1's CSS animates the
emitted classes.
"""

from src.ui.fishbowl.render.avatar import render_avatar
from src.ui.fishbowl.render.mindcard import render_mindcard

__all__ = ["render_avatar", "render_mindcard"]
