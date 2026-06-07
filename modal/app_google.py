"""Google model-serving app (Gemma family).

Deploy:    modal deploy modal/app_google.py
Serve dev: modal serve modal/app_google.py

Gemma repos are gated: create the ``huggingface-secret`` (HF_TOKEN=...) and
accept the model license on Hugging Face before deploying. Each model gets its
own OpenAI-compatible endpoint (one per model in the provider's catalogue entry).
"""

from __future__ import annotations

import modal

from catalogue import PROVIDERS
from service import register_all

_provider = PROVIDERS["google"]
app = modal.App(_provider.app)

register_all(app, _provider.models)
