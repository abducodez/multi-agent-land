"""Environment-driven settings for the observability layer.

One small frozen dataclass reads the ``MAL_*`` env vars once at
:func:`src.observability.configure` time. Keeping the parsing here (not scattered
across modules) means every layer sees the same, already-validated knobs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT_CAPACITY = 4000
_DEFAULT_TEXT_LIMIT = 4000


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class ObservabilitySettings:
    """Resolved observability configuration.

    * ``level`` — root log level (``DEBUG`` surfaces full prompts + memory).
    * ``fmt`` — terminal log format: ``text`` (human) or ``json`` (structured).
    * ``tracing`` — span sink: ``off`` | ``console`` | ``memory`` | ``both``.
      ``memory`` (default) feeds the in-app Gradio Telemetry panel with zero
      terminal noise; ``console`` also prints spans; ``both`` does each.
    * ``store_capacity`` — ring-buffer size for logs/spans kept for the UI.
    * ``store_text_limit`` — prompt/memory truncation length in stored snapshots
      (the full text still reaches the terminal at ``DEBUG``).
    """

    level: str = "INFO"
    fmt: str = "text"
    tracing: str = "memory"
    store_capacity: int = _DEFAULT_CAPACITY
    store_text_limit: int = _DEFAULT_TEXT_LIMIT

    @classmethod
    def from_env(cls) -> "ObservabilitySettings":
        return cls(
            level=(os.getenv("MAL_LOG_LEVEL") or "INFO").upper(),
            fmt=(os.getenv("MAL_LOG_FORMAT") or "text").lower(),
            tracing=(os.getenv("MAL_TRACING") or "memory").lower(),
            store_capacity=_int_env("MAL_TELEMETRY_BUFFER", _DEFAULT_CAPACITY),
            store_text_limit=_int_env("MAL_TELEMETRY_TEXT_LIMIT", _DEFAULT_TEXT_LIMIT),
        )
