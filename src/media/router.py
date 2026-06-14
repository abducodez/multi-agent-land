"""Media router — the image/speech analogue of :class:`src.models.router.ModelRouter`.

Resolves a logical media capability to a concrete provider, cached, honouring an
``offline`` flag. Offline (the test/dev seam, or simply no media backend configured) it
hands back the deterministic stubs; live it hands back the OpenAI-compatible HTTP
providers. Each modality is independent — image can be live while speech is stubbed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.media.provider import (
    HTTPImageProvider,
    HTTPSpeechProvider,
    ImageProvider,
    SpeechProvider,
    StubImageProvider,
    StubSpeechProvider,
)


@dataclass
class MediaSpec:
    """A resolved binding for one media capability (mirrors models.ProfileSpec)."""

    model: str = ""
    base_url: str = ""
    api_key: str = ""
    size: str = "512x512"
    voice: str = "default"


@dataclass
class MediaRouter:
    offline: bool = False
    image_spec: MediaSpec | None = None
    speech_spec: MediaSpec | None = None
    _image: ImageProvider | None = field(default=None, init=False, repr=False)
    _speech: SpeechProvider | None = field(default=None, init=False, repr=False)

    def image_for(self) -> ImageProvider:
        if self._image is None:
            spec = self.image_spec
            if self.offline or spec is None or not spec.base_url:
                self._image = StubImageProvider()
            else:
                self._image = HTTPImageProvider(
                    model=spec.model, base_url=spec.base_url, api_key=spec.api_key, size=spec.size
                )
        return self._image

    def speech_for(self) -> SpeechProvider:
        if self._speech is None:
            spec = self.speech_spec
            if self.offline or spec is None or not spec.base_url:
                self._speech = StubSpeechProvider()
            else:
                self._speech = HTTPSpeechProvider(
                    model=spec.model, base_url=spec.base_url, api_key=spec.api_key, voice=spec.voice
                )
        return self._speech
