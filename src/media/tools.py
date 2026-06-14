"""Wire image/speech generation into the :class:`ToolRegistry` as capability-checked tools.

The commentator (and only an agent granted them) calls ``image.render`` / ``tts.speak``
exactly like ``oracle`` — the registry enforces the manifest grant first (ADR-0012). Each
tool calls the :class:`MediaRouter` then applies the **hybrid transport**: a stub artifact
is inlined as a ``data:`` URI (self-contained, keeps the no-key demo and the exported
trace working with no files); a live artifact is written under the run's media dir and
referenced by a ``/file=`` URL (so the exported ledger/trace stays lean — just a path).
"""

from __future__ import annotations

import re
from pathlib import Path

from src.media.provider import MediaResult
from src.media.router import MediaRouter
from src.tools.registry import ToolRegistry

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(part: str) -> str:
    return _UNSAFE.sub("_", part or "x")[:80] or "x"


def _to_ref(result: MediaResult, *, media_dir: Path | None, run_id: str, slug: str) -> dict:
    """Turn a :class:`MediaResult` into a JSON-serialisable feed ref via hybrid transport."""
    ext = result.mime.split("/")[-1].split(";")[0] or "bin"
    ref = {"mime": result.mime, "model_id": result.model_id, "usage": dict(result.usage)}
    # Stub output (or no writable dir) → inline; live output → a served file.
    if result.model_id.startswith("stub:") or media_dir is None:
        ref["src"] = result.data_uri()
        return ref
    out_dir = Path(media_dir) / _safe(run_id or "run")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = (out_dir / f"{_safe(slug)}.{ext}").resolve()
    path.write_bytes(result.data)
    ref["src"] = f"/file={path}"
    return ref


def register_media_tools(registry: ToolRegistry, router: MediaRouter, media_dir: Path | None = None) -> None:
    """Register ``image.render`` and ``tts.speak`` as in-process, capability-checked tools."""

    def _image(prompt: str = "", run_id: str = "", slug: str = "", style: str = "", **_: object) -> dict:
        result = router.image_for().generate(str(prompt), style=style or None)
        return _to_ref(result, media_dir=media_dir, run_id=run_id, slug=slug or "img")

    def _speak(text: str = "", run_id: str = "", slug: str = "", voice: str = "", **_: object) -> dict:
        result = router.speech_for().synthesize(str(text), voice=voice or None)
        return _to_ref(result, media_dir=media_dir, run_id=run_id, slug=slug or "tts")

    registry.register(
        "image.render",
        description="Draw an illustration of the current beat. Params: {prompt: str}.",
        run=_image,
    )
    registry.register(
        "tts.speak",
        description="Speak a line aloud as audio. Params: {text: str}.",
        run=_speak,
    )
