"""NVIDIA model-serving app (Nemotron family).

Deploy:    modal deploy modal/app_nvidia.py
Serve dev: modal serve modal/app_nvidia.py

Each model gets its own OpenAI-compatible endpoint at
``https://<workspace>--nvidia-llms-<endpoint-name>.modal.run/v1`` (one per model in
``NVIDIA_MODELS``). Add or retune models in ``registry.py``.
"""

from __future__ import annotations

import modal

from registry import NVIDIA_MODELS
from service import register_all

app = modal.App("nvidia-llms")

register_all(app, NVIDIA_MODELS)
