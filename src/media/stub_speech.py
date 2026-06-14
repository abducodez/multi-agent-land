"""Offline speech stub — a deterministic pentatonic chime.

The critic's signature "voice" when there is no API key: a short, pleasant motif seeded
by the line, so the no-key demo proves the audio slot works (and stays reproducible)
without shipping a heavy TTS model. Live runs route to a real small TTS instead.

Pure stdlib (``wave`` + ``struct`` + ``math``), cross-platform. Same text → same WAV.
"""

from __future__ import annotations

import hashlib
import math
import struct
import wave
from io import BytesIO

_RATE = 16000
_NOTE_SECONDS = 0.18
# C-major pentatonic — any subset sounds consonant, so a hash-picked motif is always pleasant.
_SCALE = (261.63, 293.66, 329.63, 392.00, 440.00, 523.25)
_AMPLITUDE = 11000


def chime(text: str, voice: str | None = None) -> tuple[bytes, float]:
    """Return ``(wav_bytes, seconds)`` for a deterministic short chime."""
    digest = hashlib.sha256(f"{text}|{voice or ''}".encode("utf-8")).digest()
    # The voice/text hash tints the base pitch a little, so different lines sound distinct.
    pitch = 0.92 + (digest[0] / 255.0) * 0.4
    n_notes = 5 + (digest[1] % 4)  # 5–8 notes
    per_note = int(_RATE * _NOTE_SECONDS)

    frames = bytearray()
    for k in range(n_notes):
        freq = _SCALE[digest[(2 + k) % len(digest)] % len(_SCALE)] * pitch
        for i in range(per_note):
            env = math.sin(math.pi * i / per_note)  # gentle attack/decay → a soft bell, not a buzz
            sample = int(env * _AMPLITUDE * math.sin(2 * math.pi * freq * (i / _RATE)))
            frames += struct.pack("<h", sample)

    buf = BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(bytes(frames))
    return buf.getvalue(), round(n_notes * _NOTE_SECONDS, 3)
