"""MCP server exposing the built-in tools over the Model Context Protocol.

This is the *server* half of the optional MCP transport (ADR-0017).  It wraps the
same in-process tool functions (`src/tools/builtins.py`) as MCP tools using the
official SDK's FastMCP, so a tool's behaviour is identical whether an agent
reaches it in-process or over MCP.  ``oracle`` is exposed first; adding a tool is
one `@mcp.tool` decorator that delegates to the shared implementation.

Run as a stdio server::

    python -m src.tools.mcp_server

The capability check still lives on the *client* side, behind the registry
(ADR-0012): this server is pure transport and trusts the registry to have already
authorised the call.  ``mcp`` is a top-level import here because this module is
only ever imported when the server is being run — the package and the app import
cleanly without it (see ``src/tools/mcp_client.py`` for the lazy client side).
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from src.tools.builtins import oracle as _oracle

#: Stable server name advertised to clients; also used by the client config gate.
SERVER_NAME = "multi-agent-land-tools"


def build_server() -> FastMCP:
    """Construct the FastMCP server with every exposed built-in tool registered.

    Return types are annotated as ``dict[str, str]`` so FastMCP emits structured
    content the client can read back without re-parsing prose; the client also
    tolerates plain text JSON for robustness.
    """
    mcp = FastMCP(SERVER_NAME)

    @mcp.tool(
        name="oracle",
        description="Draw a single cryptic omen for the current scene. Params: {seed: str}.",
    )
    def oracle(seed: str = "") -> dict[str, str]:
        """Deterministic omen — delegates to the shared in-process implementation."""
        return _oracle(seed=seed)

    return mcp


def main() -> None:
    """Entry point: serve the tools over stdio JSON-RPC."""
    build_server().run("stdio")


if __name__ == "__main__":
    main()
