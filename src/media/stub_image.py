"""Offline image stub — a deterministic, procedurally-drawn forest scene.

Mirrors :class:`~src.models.provider.DeterministicTinyModel`: hash the prompt, draw
something charming and byte-identical, no network. This is what keeps the no-key demo
*delightful* (a real little Thousand Token Wood vista) rather than a grey placeholder.

Pure Pillow + stdlib, in-process, cross-platform. Same prompt → same PNG bytes.
"""

from __future__ import annotations

import colorsys
import hashlib
from io import BytesIO

_W, _H = 448, 256


class _Bytes:
    """A tiny deterministic source of ints/units, cycling a SHA-256 digest."""

    def __init__(self, seed: str) -> None:
        self._d = hashlib.sha256(seed.encode("utf-8")).digest()
        self._i = 0

    def _next(self) -> int:
        b = self._d[self._i % len(self._d)]
        self._i += 1
        return b

    def unit(self) -> float:
        return self._next() / 255.0

    def span(self, lo: int, hi: int) -> int:
        if hi <= lo:
            return lo
        r = (self._next() << 8) | self._next()
        return lo + r * (hi - lo) // 65535


def _hsl(h: float, s: float, lightness: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb(h, lightness, s)
    return (int(r * 255), int(g * 255), int(b * 255))


def _lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), int(a[2] + (b[2] - a[2]) * t))


def draw_forest(prompt: str, style: str | None = None) -> bytes:
    """Render a deterministic phosphor-toned forest scene as PNG bytes."""
    from PIL import Image, ImageDraw

    rnd = _Bytes(f"{prompt}|{style or ''}")
    img = Image.new("RGB", (_W, _H))
    draw = ImageDraw.Draw(img)

    # Sky: a vertical gradient between two seeded twilight hues.
    top = _hsl(rnd.unit(), 0.55, 0.16)
    bottom = _hsl(rnd.unit(), 0.5, 0.34)
    for y in range(_H):
        draw.line([(0, y), (_W, y)], fill=_lerp(top, bottom, y / (_H - 1)))

    # A moon, sometimes.
    if rnd._next() % 2:
        mx, my, r = rnd.span(40, _W - 40), rnd.span(22, 84), rnd.span(13, 26)
        draw.ellipse([mx - r, my - r, mx + r, my + r], fill=(244, 243, 222))

    # Three layers of trees, back (dark) to front (lighter), rooted near the horizon.
    horizon = int(_H * 0.6)
    for layer in range(3):
        shade = 16 + layer * 9
        for _ in range(rnd.span(5, 9)):
            x = rnd.span(0, _W)
            base = horizon + layer * 17 + rnd.span(0, 10)
            tw, th = rnd.span(13, 30), rnd.span(38, 92)
            draw.polygon([(x, base - th), (x - tw, base), (x + tw, base)], fill=(shade, shade + 20, shade + 8))
            draw.rectangle([x - 3, base, x + 3, base + 9], fill=(40, 28, 20))

    # Fireflies above the canopy.
    for _ in range(rnd.span(18, 38)):
        fx, fy = rnd.span(0, _W), rnd.span(0, horizon)
        draw.ellipse([fx - 1, fy - 1, fx + 1, fy + 1], fill=(190, 240, 120))

    # A caption strip carrying a slice of the prompt (Pillow's bundled bitmap font —
    # always present, so rendering is deterministic across machines).
    caption = " ".join((prompt or "").split())[:54]
    draw.rectangle([0, _H - 22, _W, _H], fill=(5, 18, 25))
    draw.text((8, _H - 16), caption, fill=(160, 230, 215))

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
