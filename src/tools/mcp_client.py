"""MCP client adapter — resolves tools over a stdio MCP server.

This is the *client* half of the optional MCP transport (ADR-0017).  It connects
to one or more MCP servers over stdio, lists their tools (for ``describe()``), and
dispatches a call to the owning server, returning the tool's result as a plain
JSON-serialisable ``dict`` that folds into an event payload exactly like an
in-process tool.

It is wrapped by :class:`~src.tools.registry.ToolRegistry` via an injected
*resolver* (see :func:`mcp_registry_from_env`), so the registry's capability
check (``tool in manifest.tools``) still runs **first**, unchanged — MCP is only
transport, not the security boundary (ADR-0012).

The official SDK is async-only; the registry call path is synchronous, so each
call opens a short-lived stdio session via ``anyio.run``.  ``mcp`` and ``anyio``
are imported lazily inside methods so ``import src.*`` and ``import app`` work
with the package not installed — the offline in-process registry is the default.
"""
from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.types import CallToolResult

#: Default server command used by the ``MCP_ORACLE=1`` convenience gate.
DEFAULT_ORACLE_SERVER = "python -m src.tools.mcp_server"


@dataclass(frozen=True)
class MCPServerConfig:
    """How to launch one stdio MCP server: a command plus argv and env."""

    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None

    @classmethod
    def parse(cls, spec: str) -> "MCPServerConfig":
        """Parse a shell-style command line (e.g. ``"python -m src.tools.mcp_server"``)."""
        parts = shlex.split(spec)
        if not parts:
            raise ValueError("empty MCP server spec")
        return cls(command=parts[0], args=tuple(parts[1:]))


@dataclass
class MCPToolClient:
    """Connects to one stdio MCP server and brokers list/call.

    Each public method opens its own session (connect → initialize → act → close)
    via ``anyio.run``.  That keeps the sync registry interface honest and avoids a
    long-lived background event loop; the trade-off (a stdio handshake per call)
    is documented as a follow-up in ADR-0017.
    """

    server: MCPServerConfig
    _descriptions: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    # ── transport ─────────────────────────────────────────────────────────────

    def _server_params(self):
        from mcp import StdioServerParameters

        return StdioServerParameters(
            command=self.server.command,
            args=list(self.server.args),
            env=self.server.env,
        )

    async def _list_tools_async(self) -> dict[str, str]:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        async with stdio_client(self._server_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                return {t.name: (t.description or "") for t in listed.tools}

    async def _call_tool_async(self, tool: str, params: dict) -> dict:
        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        async with stdio_client(self._server_params()) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, params)
                return _result_to_dict(tool, result)

    # ── sync surface used by the registry ───────────────────────────────────────

    def list_tools(self) -> dict[str, str]:
        """``{name: description}`` for every tool this server advertises (cached)."""
        if not self._descriptions:
            import anyio

            self._descriptions = anyio.run(self._list_tools_async)
        return self._descriptions

    def has(self, tool: str) -> bool:
        return tool in self.list_tools()

    def call(self, tool: str, params: dict) -> dict:
        """Dispatch *tool* over MCP and return its dict result."""
        import anyio

        return anyio.run(self._call_tool_async, tool, params)


def _result_to_dict(tool: str, result: "CallToolResult") -> dict:
    """Coerce an MCP ``CallToolResult`` into a plain JSON-serialisable dict.

    Prefers ``structuredContent`` (present for typed tools); otherwise JSON-parses
    the first text content block.  Raises on a tool error so the failure surfaces
    rather than entering the ledger as a malformed payload.
    """
    if getattr(result, "isError", False):
        detail = _first_text(result) or "unknown error"
        raise RuntimeError(f"MCP tool {tool!r} returned an error: {detail}")

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured

    text = _first_text(result)
    if text:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}
        return parsed if isinstance(parsed, dict) else {"result": parsed}
    return {}


def _first_text(result: "CallToolResult") -> str:
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            return text
    return ""


@dataclass
class MCPResolver:
    """Routes tool lookups/calls across one or more MCP servers by tool name.

    Satisfies :class:`~src.tools.registry.ToolResolver`.  The first server that
    advertises a tool owns it; descriptions come from the server's own listing so
    the prompt block matches what the server exposes.
    """

    clients: tuple[MCPToolClient, ...]

    def _owner(self, tool: str) -> MCPToolClient | None:
        for client in self.clients:
            if client.has(tool):
                return client
        return None

    def has(self, tool: str) -> bool:
        return self._owner(tool) is not None

    def describe(self, tool: str) -> str:
        owner = self._owner(tool)
        return owner.list_tools().get(tool, "") if owner is not None else ""

    def call(self, tool: str, params: dict) -> dict:
        owner = self._owner(tool)
        if owner is None:
            raise KeyError(f"no MCP server advertises tool {tool!r}")
        return owner.call(tool, params)


def server_configs_from_env(env: dict[str, str] | None = None) -> list[MCPServerConfig]:
    """Parse the MCP config gate into server launch configs (empty when unset).

    Two equivalent gates (documented in ADR-0017):

      * ``MCP_SERVERS`` — a list of stdio command lines separated by ``::``, e.g.
        ``"python -m src.tools.mcp_server"`` or
        ``"python -m src.tools.mcp_server :: node other-server.js"``.
      * ``MCP_ORACLE=1`` — convenience shorthand for the built-in oracle server
        (``python -m src.tools.mcp_server``); ignored if ``MCP_SERVERS`` is set.

    When neither is set this returns ``[]`` and the registry stays fully
    in-process — the offline default the test-suite exercises.
    """
    source = os.environ if env is None else env
    raw = (source.get("MCP_SERVERS") or "").strip()
    if raw:
        return [MCPServerConfig.parse(spec) for spec in raw.split("::") if spec.strip()]
    if source.get("MCP_ORACLE", "").strip() in {"1", "true", "True"}:
        return [MCPServerConfig.parse(DEFAULT_ORACLE_SERVER)]
    return []


def mcp_resolver_from_env(env: dict[str, str] | None = None) -> MCPResolver | None:
    """Build an :class:`MCPResolver` from the env gate, or ``None`` if unconfigured."""
    configs = server_configs_from_env(env)
    if not configs:
        return None
    return MCPResolver(clients=tuple(MCPToolClient(server=c) for c in configs))
