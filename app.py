"""Root entry point — a thin shim over the FISHBOWL Gradio shell.

The app itself lives in ``src.ui.fishbowl.app`` (Unit 9).  This shim keeps ``uv run
app.py`` working and preserves the no-API-key / offline behaviour: the deterministic
stub drives the cast so the demo is reproducible on stage with no credentials.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_dotenv(filename: str = ".env") -> None:
    """Load ``KEY=VALUE`` pairs from a sibling ``.env`` into the environment, if present.

    Real environment variables win (``setdefault``) — this only fills gaps, never
    overrides what the shell or CI already set.  Parsed in Python, not the shell, so
    values with shell-special characters load correctly: a Neon ``DATABASE_URL``'s
    ``&``, a JSON ``MEMORY_INDEX_CONFIG`` — all of which silently abort ``source .env``.
    Absent ``.env`` → no-op, preserving the offline-by-default behaviour (no keys, stub).
    """
    path = Path(__file__).resolve().parent / filename
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]  # strip matching surrounding quotes
        # Skip empty values: a blank `KEY=` in .env means "not configured", and
        # setting it to "" would shadow `os.getenv(KEY, default)` callers (e.g.
        # Gradio's GRADIO_SERVER_PORT) that expect absent → default.
        if key and value:
            os.environ.setdefault(key, value)


# Load .env BEFORE importing the app: the registry/router/ledger/memory all read the
# environment at import and on Summon, so credentials must be present first.  Skipped
# under pytest so the test suite stays hermetically offline (no .env bleed into tests).
if "pytest" not in sys.modules:
    _load_dotenv()
    # Initialise logging + tracing before the app imports so every layer's logger
    # and spans are wired from the first import (ADR-0024).  Reads MAL_* env vars;
    # skipped under pytest so the suite stays hermetic (instrumentation auto-configures).
    from src import observability as _obs  # noqa: E402

    _obs.configure()

from src.ui.fishbowl.app import demo, launch  # noqa: E402  (must follow _load_dotenv)

__all__ = ["demo", "launch", "_load_dotenv"]

if __name__ == "__main__":
    launch()
