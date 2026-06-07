"""Built-in in-process tools + the default tool registry.

The ``oracle`` tool is deterministic (hash of its input) so tool-using scenarios
stay reproducible offline — the same scene always draws the same omen.  It exists
to exercise the capability contract end-to-end; replace or add tools (including
MCP-server-backed ones) without touching any agent code.

By default the registry resolves tools in-process (the offline default).  When the
MCP transport is configured via the environment (``MCP_SERVERS`` or
``MCP_ORACLE=1``, see ADR-0017), the same registry instead resolves tools over an
MCP server — the capability check is unchanged either way.  The MCP client is
imported lazily inside the gate, so ``import src.*`` and ``import app`` never
require ``mcp``.
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
    """Build the tool registry, gated to MCP transport when configured.

    Offline default: every tool is an in-process callable.  If the MCP gate is set
    (``MCP_SERVERS`` / ``MCP_ORACLE=1``), an MCP resolver is attached and granted
    tools resolve over the configured server(s) instead — the capability check in
    :meth:`ToolRegistry.call` still runs first, so the security boundary is
    identical across transports (ADR-0017).
    """
    registry = ToolRegistry()
    resolver = _mcp_resolver()
    if resolver is None:
        # Offline / default path: register the in-process tools.
        registry.register(
            "oracle",
            description="Draw a single cryptic omen for the current scene. Params: {seed: str}.",
            run=oracle,
        )
    else:
        # MCP path: leave the registry's in-process table empty so granted tools
        # resolve over MCP; the capability check is unaffected.
        registry.set_resolver(resolver)
    return registry


def _mcp_resolver():
    """Return an MCP resolver if the env gate is set, else ``None`` (lazy import)."""
    from src.tools.mcp_client import mcp_resolver_from_env

    return mcp_resolver_from_env()
