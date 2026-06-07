"""Tool layer — the capability contract between agents and tools.

The ToolRegistry brokers tool calls and enforces per-agent capability grants
(``manifest.tools``).  Tools here are in-process callables; the same contract
fronts MCP servers in a later phase (see docs/architecture/tool-contract.md).
"""

from src.tools.registry import CapabilityViolation, ToolRegistry, ToolSpec

__all__ = ["ToolRegistry", "ToolSpec", "CapabilityViolation"]
