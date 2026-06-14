"""Build a :class:`MediaRouter` from ``config/media.yaml`` + the Modal workspace.

Mirrors how ``Registry`` builds the text :class:`ModelRouter`, with one deliberate
difference: media is experimental and additive, so when no media backend is configured it
**gracefully falls back to the deterministic stub** instead of refusing to start (the text
path is strict — see ``config/models.yaml``). That keeps the commentator's beat working in
the no-key demo and the test suite, and lights up live the moment the media apps are
deployed and ``config/media.yaml`` points at them.

Media endpoints use the same Modal URL convention as text models
(``https://{MODAL_WORKSPACE}--{app}-{endpoint}.modal.run/v1``) but are resolved here from
self-describing ``config/media.yaml`` fields (``app`` / ``endpoint`` / ``model``) rather
than the text catalogue — so the image/TTS services stay cleanly separate from the
chat-model catalogue the Lab and router read. A per-modality base-url override
(``MEDIA_IMAGE_BASE_URL`` / ``MEDIA_SPEECH_BASE_URL``) is the escape hatch for a non-Modal
or gateway endpoint. Calls go through the OpenAI SDK, so the ``openai/`` litellm prefix the
text path uses is irrelevant here — ``model`` is the bare served id.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from src.media.router import MediaRouter, MediaSpec

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _config_dir() -> Path:
    # Mirrors src.core.registry.DEFAULT_CONFIG_DIR without importing it (no coupling).
    return Path(os.getenv("MAL_CONFIG_DIR") or (_REPO_ROOT / "config"))


def media_output_dir() -> Path:
    """Where live media artifacts are written (and served from via ``/file=``).

    Shared by the tool that writes files and the app that allow-lists the dir at launch."""
    return Path(os.getenv("MAL_MEDIA_DIR") or (_REPO_ROOT / "runs" / "media"))


def _load_media_config() -> dict:
    path = _config_dir() / "media.yaml"
    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _base_url(kind: str, app: str, endpoint: str, source: dict[str, str]) -> str:
    """Resolve the OpenAI-compatible base URL for a modality, or ``""`` (→ stub it).

    A per-modality override wins (``MEDIA_IMAGE_BASE_URL`` / ``MEDIA_SPEECH_BASE_URL``);
    otherwise the Modal workspace + app + endpoint convention; otherwise empty."""
    override = source.get(f"MEDIA_{kind.upper()}_BASE_URL", "").strip()
    if override:
        return override.rstrip("/")
    workspace = source.get("MODAL_WORKSPACE", "").strip()
    if workspace and app and endpoint:
        return f"https://{workspace}--{app}-{endpoint}.modal.run/v1"
    return ""


def media_backend_available(env: dict[str, str] | None = None) -> bool:
    """True when a media inference backend is reachable (Modal workspace or an override)."""
    source = os.environ if env is None else env
    return bool(
        source.get("MODAL_WORKSPACE", "").strip()
        or source.get("MEDIA_IMAGE_BASE_URL", "").strip()
        or source.get("MEDIA_SPEECH_BASE_URL", "").strip()
    )


def _spec(kind: str, block: dict, source: dict[str, str]) -> MediaSpec | None:
    """Resolve a ``{default: {app, endpoint, model, size/voice}}`` block to a live spec.

    Returns ``None`` (→ the router stubs this modality) when there is no model id or the
    base URL can't be built — never raises, so a half-configured media.yaml degrades to the
    stub rather than breaking a run."""
    default = (block or {}).get("default") or {}
    model = str(default.get("model") or "")
    if not model:
        return None
    base_url = _base_url(kind, str(default.get("app", "")), str(default.get("endpoint", "")), source)
    if not base_url:
        return None
    return MediaSpec(
        model=model,
        base_url=base_url,
        api_key=source.get("MODAL_LLM_KEY", "").strip(),
        size=str(default.get("size", "512x512")),
        voice=str(default.get("voice", "default")),
    )


def build_media_router(env: dict[str, str] | None = None) -> MediaRouter:
    """Construct the media router from config + backend, defaulting safely to stubs."""
    source = dict(os.environ if env is None else env)
    cfg = _load_media_config()
    if cfg.get("offline") is True:
        return MediaRouter(offline=True)
    # Graceful divergence from the strict text path: no backend → deterministic stub,
    # so the commentator always has something to show, even with no API key.
    if not media_backend_available(source):
        return MediaRouter(offline=True)
    return MediaRouter(
        offline=False,
        image_spec=_spec("image", cfg.get("image", {}), source),
        speech_spec=_spec("speech", cfg.get("speech", {}), source),
    )
