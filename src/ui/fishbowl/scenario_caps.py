"""Fishbowl · scenario capabilities — what the Lab should show for a given run.

A pure, read-only lens over the registry that answers one question the Lab keeps
asking: *given this scenario (and any user-edited roster), which controls are even
meaningful?*  A scenario with no ``role == "judge"`` agent has no Judge section; a
scenario with no tool-granting agent shows no tool checkboxes.  Today the Lab draws
every section for every world — this derives the truth from the *effective* cast
instead, so the form adapts.

Nothing here mutates registry state: it reads the cached manifests and returns a
small, typed summary the render units consume.  See ADR-0011 / ADR-0025.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.config import ScenarioConfig
from src.core.manifest import AgentManifest
from src.core.registry import default_registry


@dataclass(frozen=True)
class ScenarioCaps:
    """The Lab-visible capability summary for one (possibly edited) run.

    All fields are derived from the *effective* cast — the user's roster override
    when supplied, otherwise the scenario's own ``cast``.  ``judge`` is the first
    ``role == "judge"`` manifest in that cast (or None → no Judge section).
    ``tool_agents`` maps each tool-granting agent's name to its granted tool ids
    (only agents that may use tools, so the Lab knows where to draw a tool picker).
    ``available_agents`` is the whole registry roster (the multiselect's choices).
    """

    scenario: ScenarioConfig
    judge: AgentManifest | None
    cast: list[AgentManifest] = field(default_factory=list)
    tool_agents: dict[str, list[str]] = field(default_factory=dict)
    available_agents: list[AgentManifest] = field(default_factory=list)

    @property
    def has_judge(self) -> bool:
        """True when the effective cast contains a judge (→ draw the Judge section)."""
        return self.judge is not None

    @property
    def has_tools(self) -> bool:
        """True when any agent in the effective cast may reach for a tool."""
        return bool(self.tool_agents)

    @property
    def cast_names(self) -> list[str]:
        """The effective cast's agent names, in cast order."""
        return [m.name for m in self.cast]

    @property
    def worker_cast(self) -> list[AgentManifest]:
        """The non-judge cast — the minds bound to models under §03."""
        return [m for m in self.cast if m.role != "judge"]


def _effective_cast_names(scenario: ScenarioConfig, cast_override: list[str] | None) -> list[str]:
    """The roster the UI should reflect: the override when given, else the scenario's.

    A blank/empty override is treated as "no override" so a cleared multiselect never
    silently empties the cast; deduped, order-preserving, so a stray repeat is harmless.
    """
    source = cast_override if cast_override else list(scenario.cast)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in source:
        if name and name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _judge_in(cast: list[AgentManifest]) -> AgentManifest | None:
    """The first ``role == "judge"`` manifest in *cast*, or None."""
    for manifest in cast:
        if manifest.role == "judge":
            return manifest
    return None


def scenario_ui_caps(
    scenario: ScenarioConfig | str,
    *,
    cast_override: list[str] | None = None,
) -> ScenarioCaps:
    """Derive the Lab's visible-control summary for *scenario*.

    *scenario* may be a :class:`ScenarioConfig` or its internal name (resolved against
    the registry).  ``cast_override`` lets the caller pass the user-edited roster so the
    summary reflects what the form will actually run (e.g. dropping the judge hides the
    Judge section).  Unknown agent names in the override or scenario cast are skipped —
    the summary stays coherent and the deeper ``collect_world_config`` validation owns
    the hard errors.

    Pure and read-only: reuses the registry's cached manifests, never mutating them.
    """
    registry = default_registry()
    if isinstance(scenario, str):
        resolved = registry.scenarios.get(scenario)
        if resolved is None:
            raise ValueError(f"unknown scenario {scenario!r} (have: {sorted(registry.scenarios)})")
        scenario = resolved

    names = _effective_cast_names(scenario, cast_override)
    cast = [registry.agents[name] for name in names if name in registry.agents]

    tool_agents = {m.name: list(m.tools) for m in cast if m.tools}
    available_agents = sorted(registry.agents.values(), key=lambda m: m.name)

    return ScenarioCaps(
        scenario=scenario,
        judge=_judge_in(cast),
        cast=cast,
        tool_agents=tool_agents,
        available_agents=available_agents,
    )
