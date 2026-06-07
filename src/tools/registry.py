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
from typing import Protocol

from src.core.manifest import AgentManifest


class CapabilityViolation(RuntimeError):
    """Raised when an agent calls a tool its manifest does not grant."""


@dataclass
class ToolSpec:
    name: str
    description: str
    run: Callable[..., dict]


class ToolResolver(Protocol):
    """A transport that backs tools not registered in-process (e.g. an MCP server).

    Used only *after* the capability check passes — MCP is transport, never the
    security boundary (ADR-0012, ADR-0017).
    """

    def has(self, tool: str) -> bool: ...

    def describe(self, tool: str) -> str: ...

    def call(self, tool: str, params: dict) -> dict: ...


class ToolRegistry:
    """Capability-checked broker.

    Tools resolve in-process by default.  An optional *resolver* (set via
    :meth:`set_resolver`) backs tools that are not registered locally — the MCP
    transport plugs in here.  The capability grant (``tool in manifest.tools``) is
    always enforced first, before either path runs, so swapping transports never
    weakens the security boundary.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._resolver: ToolResolver | None = None

    def register(self, name: str, description: str, run: Callable[..., dict]) -> None:
        self._tools[name] = ToolSpec(name=name, description=description, run=run)

    def set_resolver(self, resolver: ToolResolver | None) -> None:
        """Attach a transport (e.g. MCP) for tools not registered in-process."""
        self._resolver = resolver

    def has(self, name: str) -> bool:
        if name in self._tools:
            return True
        return self._resolver is not None and self._resolver.has(name)

    def describe(self, names: list[str]) -> str:
        """Render the granted tools for prompt injection (skips unknown names).

        In-process registrations take precedence; otherwise a resolver-backed
        description is used when available.  Unknown names are skipped exactly as
        before, so prompt assembly is unchanged across transports.
        """
        lines: list[str] = []
        for n in names:
            if n in self._tools:
                lines.append(f"- {self._tools[n].name}: {self._tools[n].description}")
            elif self._resolver is not None and self._resolver.has(n):
                lines.append(f"- {n}: {self._resolver.describe(n)}")
        return "\n".join(lines)

    def call(self, agent_name: str, manifest: AgentManifest, tool: str, params: dict) -> dict:
        """Dispatch *tool* for *agent_name*, enforcing the manifest capability grant.

        The grant is checked first — a denied call raises :class:`CapabilityViolation`
        before any transport is touched, in-process or MCP.  In-process tools take
        precedence; otherwise the call is dispatched to the resolver if one backs
        the tool.  An unknown granted tool raises :class:`KeyError` as before.
        """
        if tool not in manifest.tools:
            raise CapabilityViolation(
                f"{agent_name!r} is not authorised to call tool {tool!r} "
                f"(granted: {manifest.tools})"
            )
        if tool in self._tools:
            return self._tools[tool].run(**params)
        if self._resolver is not None and self._resolver.has(tool):
            return self._resolver.call(tool, params)
        raise KeyError(f"unknown tool {tool!r} (registered: {sorted(self._tools)})")
