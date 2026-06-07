# ADR-0017: MCP Tool Transport Behind the Capability Layer

## Status

Accepted

## Context

Tools are the fourth stable contract (ADR-0012): an agent never holds a tool
directly; it asks the `ToolRegistry`, which enforces the agent's capability grant
(`tool in manifest.tools`) before dispatching. Until now a tool was always an
in-process callable — a `(name, description, run)` triple where `run(**params)`
returns a JSON-serialisable dict the calling agent folds into its event. The
contract was deliberately shaped (`docs/architecture/tool-contract.md`) so the
same interface could later front a tool running out-of-process over the Model
Context Protocol (MCP), invisible to agents.

We want to realise that: expose the built-in tools over an MCP server and let the
registry resolve granted tools over MCP when configured — without weakening the
security boundary and without making the offline system depend on a server or on
the `mcp` package being installed. The offline, in-process path must remain the
default the test-suite exercises.

## Decision

Add **MCP as an optional tool transport behind the existing capability layer**.
MCP changes *how* a granted tool is reached, never *whether* it may be called: the
capability check runs first, unchanged, in both transports.

**Server.** `src/tools/mcp_server.py` builds a FastMCP server
(`mcp.server.fastmcp.FastMCP`) that registers each exposed built-in as an MCP
tool delegating to the *same* implementation in `src/tools/builtins.py`. `oracle`
is exposed first; adding a tool is one `@mcp.tool` decorator. Return types are
annotated (`dict[str, str]`) so FastMCP emits structured content. It runs as a
stdio JSON-RPC server (`python -m src.tools.mcp_server`). The server is pure
transport: it trusts the client-side registry to have already authorised the
call, so it performs no capability check itself.

**Client adapter.** `src/tools/mcp_client.py` connects to one or more stdio MCP
servers, lists their tools (for `describe()`), and dispatches a call to the owning
server, coercing the MCP `CallToolResult` back into a plain dict (preferring
`structuredContent`, falling back to JSON-parsing the first text block; a tool
error raises rather than entering the ledger). The official SDK is async-only and
the registry call path is synchronous, so each call opens a short-lived stdio
session via `anyio.run` (connect → initialize → act → close). `mcp` and `anyio`
are imported lazily inside methods.

**Resolver behind the registry — capability check first.** `ToolRegistry` gains an
optional `ToolResolver` (`set_resolver`). `call(...)` enforces the grant first and
raises `CapabilityViolation` on a denied call *before any transport is touched*;
only then does it dispatch — in-process if the tool is registered locally,
otherwise to the resolver. `describe()` prefers in-process descriptions and falls
back to the resolver's, skipping unknown names exactly as before, so prompt
assembly is identical across transports. `MCPResolver` (in `mcp_client.py`) routes
lookups/calls across the configured servers by tool name and satisfies that
protocol.

**Config gate.** `default_tool_registry()` returns the in-process registry by
default. When the MCP gate is set it attaches an `MCPResolver` and leaves the
in-process table empty, so granted tools resolve over MCP instead. Two equivalent
gates:

  * `MCP_SERVERS` — `::`-separated stdio command lines, e.g.
    `python -m src.tools.mcp_server` or
    `python -m src.tools.mcp_server :: node other-server.js`.
  * `MCP_ORACLE=1` — shorthand for the built-in oracle server
    (`python -m src.tools.mcp_server`); ignored when `MCP_SERVERS` is set.

When neither is set the registry stays fully in-process — the offline default.

**Dependency.** `mcp` is a new optional `mcp` extra in `pyproject.toml`. The
server module top-imports `mcp` because it is only imported when the server is
being run; the client, registry, and builtins import `mcp`/`anyio` lazily, so
`import src.*` and `import app` work with the package not installed and the gate
unset.

## Consequences

- The capability grant is the security boundary in both transports: a denied call
  raises `CapabilityViolation` before any in-process or MCP work, proven without a
  live server via a spy resolver (`tests/test_mcp.py`).
- The offline, in-process path is the default and unchanged: `tests/test_tools.py`
  and `oracle-grove` pass with no server running and `mcp` not installed. With
  `mcp` absent the MCP-specific tests skip; the suite stays ≥226 green offline.
- The transport is invisible to behaviour: for a given seed the omen drawn over
  MCP is byte-identical to the in-process one (the `oracle` implementation is
  shared), so `oracle-grove` produces the same ledger in both modes.
- Tool results stay JSON-serialisable dicts that fold into event payloads
  (`{"omen": …}`) regardless of transport.
- A short-lived stdio session per call (connect/initialize/teardown) trades steady
  performance for a simple, honest sync interface and no long-lived background
  loop. Follow-ups: pool or persist sessions for hot tools; manage server-process
  lifecycle (health, restart) rather than spawning per call; expose the
  in-process-vs-MCP transport on the stats panel; add a remote (SSE / streamable
  HTTP) transport for tools that cannot be spawned locally.
