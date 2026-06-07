"""Back-compat re-export of the model catalogue.

The declarative catalogue moved to :mod:`catalogue` (stdlib-only, shared with the
engine — see its module docstring for why). This module re-exports the
per-provider lists so existing imports, scripts, and docs keep working. New code
should import from ``catalogue`` directly.
"""

from __future__ import annotations

from catalogue import (
    ALL_MODELS,
    GOOGLE_MODELS,
    NVIDIA_MODELS,
    OPENBMB_MODELS,
    ModelConfig,
)

__all__ = [
    "ModelConfig",
    "NVIDIA_MODELS",
    "OPENBMB_MODELS",
    "GOOGLE_MODELS",
    "ALL_MODELS",
]
