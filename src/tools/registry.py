"""Tool registry — capability-checked broker between agents and tools.

This is the fourth stable contract (ADR-0012).  An agent never holds a tool
directly; it asks the registry, which checks the agent's manifest grant before
dispatching.  The Artist gets ``image-gen``; the Critic does not — enforced here,
not by convention.

Tools are registered as ``(name, description, run)`` triples.  ``run(**params)``
returns a JSON-serialisable dict that the calling agent folds into its event.
In-process callables and MCP-server-backed tools both satisfy this interface, so
swapping a local stub for a real MCP server is invisible to agents.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from src.core.manifest import AgentManifest


class CapabilityViolation(RuntimeError):
    """Raised when an agent calls a tool its manifest does not grant."""


@dataclass
class ToolSpec:
    name: str
    description: str
    run: Callable[..., dict]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, name: str, description: str, run: Callable[..., dict]) -> None:
        self._tools[name] = ToolSpec(name=name, description=description, run=run)

    def has(self, name: str) -> bool:
        return name in self._tools

    def describe(self, names: list[str]) -> str:
        """Render the granted tools for prompt injection (skips unknown names)."""
        lines = [f"- {self._tools[n].name}: {self._tools[n].description}" for n in names if n in self._tools]
        return "\n".join(lines)

    def call(self, agent_name: str, manifest: AgentManifest, tool: str, params: dict) -> dict:
        """Dispatch *tool* for *agent_name*, enforcing the manifest capability grant."""
        if tool not in manifest.tools:
            raise CapabilityViolation(
                f"{agent_name!r} is not authorised to call tool {tool!r} "
                f"(granted: {manifest.tools})"
            )
        if tool not in self._tools:
            raise KeyError(f"unknown tool {tool!r} (registered: {sorted(self._tools)})")
        return self._tools[tool].run(**params)
