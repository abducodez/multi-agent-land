"""Tool layer — the capability contract between agents and tools.

The ToolRegistry brokers tool calls and enforces per-agent capability grants
(``manifest.tools``).  Tools resolve in-process by default; the same contract also
fronts MCP servers when the transport is configured (``MCP_SERVERS`` /
``MCP_ORACLE``), with the capability check always running first — see ADR-0017 and
docs/architecture/tool-contract.md.  The MCP client/server modules are imported
lazily, so importing this package never requires ``mcp``.
"""

from src.tools.registry import CapabilityViolation, ToolRegistry, ToolSpec

__all__ = ["ToolRegistry", "ToolSpec", "CapabilityViolation"]
