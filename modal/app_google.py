"""Google model-serving app (Gemma family).

Deploy:    modal deploy modal/app_google.py
Serve dev: modal serve modal/app_google.py

Gemma repos are gated: create the ``huggingface-secret`` (HF_TOKEN=...) and
accept the model license on Hugging Face before deploying. Each model gets its
own OpenAI-compatible endpoint (one per model in ``GOOGLE_MODELS``).
"""

from __future__ import annotations

import modal

from registry import GOOGLE_MODELS
from service import register_all

app = modal.App("google-llms")

register_all(app, GOOGLE_MODELS)
