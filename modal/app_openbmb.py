"""OpenBMB model-serving app (MiniCPM family).

Deploy:    modal deploy modal/app_openbmb.py
Serve dev: modal serve modal/app_openbmb.py

Each model gets its own OpenAI-compatible endpoint (one per model in
``OPENBMB_MODELS``). MiniCPM-o is omni-modal; see ``registry.py`` for the
multimodal limits and the extra media backends baked into its image.
"""

from __future__ import annotations

import modal

from registry import OPENBMB_MODELS
from service import register_all

app = modal.App("openbmb-llms")

register_all(app, OPENBMB_MODELS)
