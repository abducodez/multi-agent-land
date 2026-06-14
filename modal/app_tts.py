"""Text-to-speech serving app (VoxCPM2 — a 2B MiniCPM-4-backbone TTS).

Deploy:    modal deploy modal/app_tts.py   (or: uv run scripts/deploy_modal.py tts)
Serve dev: modal serve modal/app_tts.py

Each model in the media catalogue gets its own OpenAI-compatible ``/v1/audio/speech``
endpoint at ``…--audio-tts-<endpoint>.modal.run``.
"""

from __future__ import annotations

import modal

from media_catalogue import TTS_APP, TTS_MODELS
from tts_service import register_all

app = modal.App(TTS_APP)

register_all(app, TTS_MODELS)
