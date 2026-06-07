"""NVIDIA model-serving app (Nemotron family).

Deploy:    modal deploy modal/app_nvidia.py
Serve dev: modal serve modal/app_nvidia.py

Each model gets its own OpenAI-compatible endpoint at
``https://<workspace>--nvidia-llms-<endpoint-name>.modal.run/v1`` (one per model in
the provider's catalogue entry). Add or retune models in ``catalogue.py``.
"""

from __future__ import annotations

import modal

from catalogue import PROVIDERS
from service import register_all

_provider = PROVIDERS["nvidia"]
app = modal.App(_provider.app)

register_all(app, _provider.models)
