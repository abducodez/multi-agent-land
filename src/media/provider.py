"""Media providers — the image/speech analogue of :mod:`src.models.provider`.

Two capabilities, each with the same two implementations as the text layer: a
deterministic offline **stub** (so the no-key demo and the zero-mock tests stay whole)
and a **live** OpenAI-compatible HTTP provider (an image-generations / audio-speech
endpoint served on Modal, ADR-0015). A :class:`MediaResult` mirrors a model provider's
``model_id`` + ``last_usage`` so a media call is attributable and meterable like any
other call. Routing (offline vs live, which endpoint) lives in :mod:`src.media.router`.
"""

from __future__ import annotations

import base64
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src import observability as obs
from src.media.stub_image import draw_forest
from src.media.stub_speech import chime

# A consistent look for the live image model — the engine stays clean; the prompt
# engineering rides here, next to the call that uses it.
_IMAGE_STYLE = "a whimsical painterly storybook illustration, soft phosphor glow, no text:"


def _media_timeout() -> float:
    """Per-call wall-clock budget for a live media request (env ``MEDIA_TIMEOUT_S``).

    Media is best-effort garnish: a down or crash-looping endpoint must fail FAST and
    let the beat degrade to text, never block the synchronous turn. The OpenAI SDK
    otherwise defaults to a 600s timeout with 2 retries (~30 min worst case), which reads
    as the whole show hanging. We cap it and disable retries. Generous enough for a warm
    few-step image / short TTS clip; tune up if a warm call legitimately needs longer."""
    try:
        return max(5.0, float(os.getenv("MEDIA_TIMEOUT_S", "120")))
    except ValueError:
        return 120.0


@dataclass
class MediaResult:
    """One synthesized artifact plus its metering — the UI turns it into a ref."""

    mime: str
    data: bytes
    model_id: str
    usage: dict[str, float] = field(default_factory=dict)
    meta: dict[str, str] = field(default_factory=dict)

    def data_uri(self) -> str:
        """A self-contained ``data:`` URI (the offline transport)."""
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"


class ImageProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, *, style: str | None = None) -> MediaResult: ...


class SpeechProvider(ABC):
    @abstractmethod
    def synthesize(self, text: str, *, voice: str | None = None) -> MediaResult: ...


# ── offline stubs (deterministic, no network) ──────────────────────────────────


@dataclass
class StubImageProvider(ImageProvider):
    variant: str = "stub:image"

    def generate(self, prompt: str, *, style: str | None = None) -> MediaResult:
        with obs.span("media.call", **{"media.system": "stub", "media.kind": "image", "media.model": self.variant}):
            png = draw_forest(prompt, style)
            obs.incr("media.calls", 1, kind="image", model=self.variant)
            return MediaResult("image/png", png, self.variant, {"images": 1, "cost_usd": 0.0}, {"prompt": prompt[:200]})


@dataclass
class StubSpeechProvider(SpeechProvider):
    variant: str = "stub:speech"

    def synthesize(self, text: str, *, voice: str | None = None) -> MediaResult:
        with obs.span("media.call", **{"media.system": "stub", "media.kind": "speech", "media.model": self.variant}):
            wav, seconds = chime(text, voice)
            obs.incr("media.calls", 1, kind="speech", model=self.variant)
            return MediaResult(
                "audio/wav",
                wav,
                self.variant,
                {"audio_seconds": seconds, "cost_usd": 0.0},
                {"voice": voice or "default"},
            )


# ── live OpenAI-compatible HTTP providers ───────────────────────────────────────


@dataclass
class HTTPImageProvider(ImageProvider):
    """Calls ``POST {base_url}/images/generations`` via the OpenAI SDK (ADR-0015)."""

    model: str
    base_url: str
    api_key: str = ""
    size: str = "512x512"

    def generate(self, prompt: str, *, style: str | None = None) -> MediaResult:
        from openai import OpenAI

        framed = f"{(style or _IMAGE_STYLE).rstrip(':')}: {prompt}".strip()
        with obs.span("media.call", **{"media.system": "openai", "media.kind": "image", "media.model": self.model}):
            # Bounded timeout, no retries: a failure degrades to a text-only beat fast (the
            # caller catches and skips media), never hanging the synchronous turn.
            client = OpenAI(
                base_url=self.base_url, api_key=self.api_key or "sk-noauth", timeout=_media_timeout(), max_retries=0
            )
            resp = client.images.generate(
                model=self.model, prompt=framed, size=self.size, n=1, response_format="b64_json"
            )
            data = base64.b64decode(resp.data[0].b64_json or "")
            obs.incr("media.calls", 1, kind="image", model=self.model)
            return MediaResult("image/png", data, self.model, {"images": 1}, {"prompt": prompt[:200]})


@dataclass
class HTTPSpeechProvider(SpeechProvider):
    """Calls ``POST {base_url}/audio/speech`` via the OpenAI SDK (ADR-0015)."""

    model: str
    base_url: str
    api_key: str = ""
    voice: str = "default"

    def synthesize(self, text: str, *, voice: str | None = None) -> MediaResult:
        from openai import OpenAI

        with obs.span("media.call", **{"media.system": "openai", "media.kind": "speech", "media.model": self.model}):
            # Bounded timeout, no retries: a failure degrades the beat to text fast (the
            # caller catches and skips audio), never hanging the synchronous turn.
            client = OpenAI(
                base_url=self.base_url, api_key=self.api_key or "sk-noauth", timeout=_media_timeout(), max_retries=0
            )
            resp = client.audio.speech.create(
                model=self.model, input=text, voice=voice or self.voice, response_format="wav"
            )
            data = resp.read() if hasattr(resp, "read") else getattr(resp, "content", b"")
            obs.incr("media.calls", 1, kind="speech", model=self.model)
            return MediaResult(
                "audio/wav", bytes(data), self.model, {"audio_seconds": 0.0}, {"voice": voice or self.voice}
            )
