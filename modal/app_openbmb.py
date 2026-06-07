"""OpenBMB model-serving app (MiniCPM family).

Deploy:    modal deploy modal/app_openbmb.py
Serve dev: modal serve modal/app_openbmb.py

Each model gets its own OpenAI-compatible endpoint (one per model in the
provider's catalogue entry). MiniCPM-o is omni-modal; see ``catalogue.py`` for the
multimodal limits and the extra media backends baked into its image.
"""

from __future__ import annotations

import modal

from catalogue import PROVIDERS
from service import register_all

_provider = PROVIDERS["openbmb"]
app = modal.App(_provider.app)

register_all(app, _provider.models)
