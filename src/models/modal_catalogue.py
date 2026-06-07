"""Bridge from the engine to the model catalogue defined under ``modal/``.

The catalogue (``modal/catalogue.py``) is the single source of truth for which
small models exist and how to call them (see its module docstring). This module
is the engine's read-only view of it: it answers "what models are there?" and
"given a casting key, what LiteLLM model string + endpoint URL should a profile
use?" — deriving the live binding from ``$MODAL_WORKSPACE`` / ``$MODAL_LLM_KEY``
so adding a model in ``modal/`` makes it bindable here with no engine edits.

**Why load by file path.** The folder is literally named ``modal``, which would
shadow the PyPI ``modal`` SDK, so ``import modal.catalogue`` is impossible. The
catalogue is deliberately stdlib-only, so we load it from its file under a
non-conflicting module name. The load is cached, dependency-free, and offline-safe
— and degrades gracefully (empty catalogue) if the file is absent, so a stripped
deployment still imports.

Nothing here reaches the network or imports Modal/vLLM. It is pure data + URL
string-building.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[2]
# The modal/ dir is at the repo root; ``MAL_MODAL_DIR`` can relocate it for an
# alternate deployment layout. The file is loaded by path, never imported by name.
_CATALOGUE_PATH = Path(os.getenv("MAL_MODAL_DIR") or (_REPO_ROOT / "modal")) / "catalogue.py"
# Deliberately NOT "modal" or "catalogue" — a unique name avoids clobbering either
# the PyPI SDK or any same-named module in sys.modules.
_MODULE_NAME = "mal_modal_catalogue"


@lru_cache(maxsize=1)
def _module() -> ModuleType | None:
    """Load (once) the stdlib-only catalogue module from its file, or ``None``.

    Cached for the process. Returns ``None`` if the file is missing or fails to
    import, so callers can degrade to the offline stub rather than crash.
    """
    path = _CATALOGUE_PATH
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, path)
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            return None
        module = importlib.util.module_from_spec(spec)
        # Register before exec: dataclasses (with `from __future__ annotations`)
        # resolve the defining module via sys.modules during class creation, so a
        # file-path load must be visible there before its body runs.
        sys.modules[_MODULE_NAME] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(_MODULE_NAME, None)
            raise
        return module
    except Exception:  # pragma: no cover - defensive: a broken catalogue → offline
        return None


# ── public read-only view ──────────────────────────────────────────────────────


def available() -> bool:
    """True when the catalogue file could be loaded."""
    return _module() is not None


def entries() -> list[dict]:
    """Every catalogue model as a plain dict (so callers don't depend on the
    catalogue's dataclasses):

    ``{key, provider, app, endpoint_name, served_model_id, profile, params_b}``.
    """
    module = _module()
    if module is None:
        return []
    return [
        {
            "key": e.key,
            "provider": e.provider,
            "app": e.app,
            "endpoint_name": e.endpoint_name,
            "served_model_id": e.served_model_id,
            "profile": e.profile,
            "params_b": e.params_b,
        }
        for e in module.entries()
    ]


def entry_by_key(key: str) -> dict | None:
    """The catalogue entry whose casting key (``endpoint_name``) is *key*, or None."""
    return next((e for e in entries() if e["key"] == key), None)


def default_key_for_profile(profile: str) -> str | None:
    """The casting key of the model the catalogue tags for *profile* (first match)."""
    return next((e["key"] for e in entries() if e["profile"] == profile), None)


def binding_for(key: str, env: dict[str, str] | None = None) -> dict:
    """Resolve a catalogue *key* into a concrete profile binding.

    Returns ``{"model", "base_url", "api_key"}`` where:

      * ``model`` is the LiteLLM model string ``openai/<served_model_id>``;
      * ``base_url`` is the endpoint's ``/v1`` URL — ``$MODAL_LLM_BASE_URL`` if set
        (a single explicit endpoint), else built from ``$MODAL_WORKSPACE`` and the
        catalogue, or ``""`` when neither is configured (→ offline stub);
      * ``api_key`` is ``$MODAL_LLM_KEY`` (vLLM accepts any token; ``""`` lets the
        transport default to the conventional ``EMPTY``).

    Raises ``KeyError`` if *key* is unknown (or the catalogue is unavailable) — a
    profile pointing at a non-existent endpoint is a config error worth surfacing.
    """
    source = os.environ if env is None else env
    module = _module()
    if module is None:
        raise KeyError(f"model catalogue unavailable ({_CATALOGUE_PATH}); cannot resolve endpoint {key!r}")
    entry = entry_by_key(key)
    if entry is None:
        known = sorted(e["key"] for e in entries())
        raise KeyError(f"unknown model endpoint {key!r}; known endpoints: {known}")

    workspace = source.get("MODAL_WORKSPACE", "").strip()
    explicit_base = source.get("MODAL_LLM_BASE_URL", "").strip()
    if explicit_base:
        base_url = explicit_base
    elif workspace:
        base_url = module.endpoint_url(entry["app"], entry["endpoint_name"], workspace)
    else:
        base_url = ""  # neither configured → not a live binding → offline stub
    return {
        "model": module.litellm_model(entry["served_model_id"]),
        "base_url": base_url,
        "api_key": source.get("MODAL_LLM_KEY", "").strip(),
    }
