"""Media capability layer — image generation + text-to-speech for agents.

The image/speech analogue of :mod:`src.models`: a provider contract with a deterministic
offline stub and a live OpenAI-compatible HTTP provider, a small router, and a
:class:`ToolRegistry` wiring so an agent reaches media through the same capability-checked
``tools:`` grant it uses for any other tool. Offline-safe by default (ADR-0012, ADR-0015).
"""

from src.media.inference import build_media_router, media_backend_available, media_output_dir
from src.media.provider import (
    ImageProvider,
    MediaResult,
    SpeechProvider,
    StubImageProvider,
    StubSpeechProvider,
)
from src.media.router import MediaRouter, MediaSpec
from src.media.tools import register_media_tools

__all__ = [
    "MediaResult",
    "ImageProvider",
    "SpeechProvider",
    "StubImageProvider",
    "StubSpeechProvider",
    "MediaRouter",
    "MediaSpec",
    "build_media_router",
    "media_backend_available",
    "media_output_dir",
    "register_media_tools",
]
