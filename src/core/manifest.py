"""Agent manifest — the stable contract between the engine and any agent plugin.

The manifest is the only thing the conductor, context-builder, and scheduler
need to know about an agent.  Nothing in the engine imports agent internals.
Adding an agent is dropping in a manifest + a handler file; no engine edits.

Four stable contracts hold the whole system together:
  1. EventSchema  — src/core/events.py
  2. LedgerAPI    — src/core/ledger.py
  3. AgentManifest — this file
  4. ToolContract — declared in tools/  (future MCP servers)
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ── type aliases ─────────────────────────────────────────────────────────────

AgentRole = Literal["worker", "judge", "observer", "reflector"]
ModelProfile = Literal["tiny", "fast", "balanced", "strong"]


# ── sub-schemas ───────────────────────────────────────────────────────────────

class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window: int = 8
    """Number of recent visible events to include in every prompt."""

    use_salience: bool = False
    """When True, rank events by salience score instead of pure recency."""

    salience_top_k: int = 8
    """How many top-scoring events to keep when salience is enabled."""

    reflection_threshold: int | None = None
    """Emit an agent.reflected event every N visible events (None = disabled)."""


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick_every: int | None = None
    """Also fire this agent every N turns regardless of subscriptions.
    None means event-driven only; 0 means every turn."""

    max_consecutive: int = 3
    """Maximum turns in a row this agent can act without a break."""


# ── manifest ─────────────────────────────────────────────────────────────────

class AgentManifest(BaseModel):
    """Declarative description of a single specialist agent.

    The conductor discovers agents via their manifests.  The manifest
    declares capability (what the agent can do), communication contract
    (what it reads and writes), and resource limits (model, memory, tools).
    No engine code needs to know which agents exist — it reads manifests
    and routes accordingly.
    """

    model_config = ConfigDict(extra="forbid")

    # Identity
    name: str
    """Unique slug.  Must match the Agent.name class attribute."""

    role: AgentRole = "worker"
    """Cognitive role: worker (produces), judge (evaluates), observer (renders),
    reflector (compacts memory)."""

    persona: str
    """Fixed identity text injected as IDENTITY in every prompt.
    Keep it tight — it occupies permanent prompt budget."""

    # Communication contract
    subscribes_to: list[str] = Field(default_factory=list)
    """Event kinds that trigger this agent.
    Example: ["world.observed", "user.injected"]
    Glob patterns not supported; use explicit kind strings."""

    may_emit: list[str] = Field(default_factory=list)
    """Event kinds this agent is permitted to emit.
    The runtime validates emitted events against this list."""

    # Scheduling
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)

    # Model
    model_profile: ModelProfile = "fast"
    """Logical profile: resolved to a concrete model name by the provider.
    tiny=<=4B, fast=<=7B, balanced=<=13B, strong=<=32B."""

    # Memory
    memory: MemoryConfig = Field(default_factory=MemoryConfig)

    # Capability grants
    tools: list[str] = Field(default_factory=list)
    """MCP server names this agent may access.  Capability-based least privilege:
    the runtime only wires the tools named here."""


# ── model profile resolution ─────────────────────────────────────────────────

_PROFILE_ENV_KEYS: dict[ModelProfile, str] = {
    "tiny": "MODEL_TINY",
    "fast": "MODEL_FAST",
    "balanced": "MODEL_BALANCED",
    "strong": "MODEL_STRONG",
}

_PROFILE_DEFAULTS: dict[ModelProfile, str] = {
    "tiny": "gpt-4o-mini",        # placeholder; swap to Qwen2.5-3B via env
    "fast": "gpt-4o-mini",
    "balanced": "gpt-4o-mini",
    "strong": "gpt-4o",
}


def resolve_model(profile: ModelProfile) -> str:
    """Return the concrete model name for a logical profile.

    Reads MODEL_TINY / MODEL_FAST / MODEL_BALANCED / MODEL_STRONG from the
    environment, then falls back to the default.  This is the only place
    model names are resolved — agent code never hard-codes a model.
    """
    import os

    env_key = _PROFILE_ENV_KEYS[profile]
    return os.getenv(env_key, "") or os.getenv("MODEL_NAME", "") or _PROFILE_DEFAULTS[profile]
