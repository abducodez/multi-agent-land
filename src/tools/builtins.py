"""Built-in in-process tools + the default tool registry.

The ``oracle`` tool is deterministic (hash of its input) so tool-using scenarios
stay reproducible offline — the same scene always draws the same omen.  It exists
to exercise the capability contract end-to-end; replace or add tools (including
MCP-server-backed ones) without touching any agent code.
"""
from __future__ import annotations

import hashlib

from src.tools.registry import ToolRegistry

_OMENS = [
    "a door that only opens for those who have forgotten why they knocked",
    "a coin that always lands on the side you did not choose",
    "a map of a place that is still making up its mind",
    "a bell that rings one second before it is struck",
    "a shadow that arrives before the thing that casts it",
    "a name that belongs to whoever says it last",
    "a staircase that counts itself differently each time",
    "a letter addressed to the reader's future regret",
]


def oracle(seed: str = "", **_: object) -> dict:
    """Return a deterministic omen for the given seed/scene text."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return {"omen": _OMENS[int(digest[:6], 16) % len(_OMENS)]}


def default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        "oracle",
        description="Draw a single cryptic omen for the current scene. Params: {seed: str}.",
        run=oracle,
    )
    return registry
