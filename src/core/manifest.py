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

    handler: str | None = None
    """Optional behaviour binding.  When set, the registry instantiates the
    ManifestAgent subclass registered under this key (for agents that call tools
    or need custom prompt logic).  When None, the generic ManifestAgent is used —
    so most agents are pure declarative config with no Python at all."""

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
    """MCP server / tool names this agent may access.  Capability-based least
    privilege: the runtime only wires the tools named here."""

    # Output shaping
    output_extra_fields: list[str] = Field(default_factory=list)
    """Additional payload fields the model is asked to emit beyond {kind, text}.
    Example: ["emotion"] -> {"kind": "...", "text": "...", "emotion": "..."}.
    Lets a scenario shape agent output without engine edits."""

    # Presentation metadata — optional, consumed by the UI presenter and ignored
    # by the engine (ADR-0021).  Additive and defaulted, so existing manifests and
    # tests are unaffected; the presenter derives sensible values when these are None.
    hue: int | None = None
    """Optional 0–360 colour hue for this agent's mind on stage.
    None → the presenter derives a stable hue from the name."""

    archetype: str | None = None
    """Optional short, human-readable archetype (e.g. "the over-thinker").
    None → the presenter derives one from the role/persona."""


# ── model profile resolution ─────────────────────────────────────────────────

_PROFILE_ENV_KEYS: dict[ModelProfile, str] = {
    "tiny": "MODEL_TINY",
    "fast": "MODEL_FAST",
    "balanced": "MODEL_BALANCED",
    "strong": "MODEL_STRONG",
}

# Static fallback model per profile — the small models served on Modal (mirrors
# the profile tags in modal/catalogue.py). resolve_model() prefers the live
# catalogue; these are used only if it cannot be read. Values are LiteLLM model
# strings (``openai/<served_id>``) for the OpenAI-compatible custom-endpoint path.
_PROFILE_DEFAULTS: dict[ModelProfile, str] = {
    "tiny": "openai/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16",
    "fast": "openai/openbmb/MiniCPM4.1-8B",
    "balanced": "openai/google/gemma-4-12B",
    "strong": "openai/google/gemma-4-26B-A4B-it",
}


def _catalogue_default(profile: ModelProfile) -> str | None:
    """The catalogue's default model for *profile* as a LiteLLM string, or None."""
    try:
        from src.models import modal_catalogue

        key = modal_catalogue.default_key_for_profile(profile)
        if key:
            entry = modal_catalogue.entry_by_key(key)
            if entry:
                return f"openai/{entry['served_model_id']}"
    except Exception:  # pragma: no cover - defensive: catalogue unavailable
        return None
    return None


def resolve_model(profile: ModelProfile) -> str:
    """Return the concrete model string for a logical profile.

    Precedence: the ``MODEL_TINY`` / ``MODEL_FAST`` / ``MODEL_BALANCED`` /
    ``MODEL_STRONG`` env override, then the catalogue's default for that tier
    (``modal/catalogue.py``), then the static fallback above.  This is the only
    place model names are resolved on the specless path — agent code never
    hard-codes a model.
    """
    import os

    override = os.getenv(_PROFILE_ENV_KEYS[profile], "")
    if override:
        return override
    return _catalogue_default(profile) or _PROFILE_DEFAULTS[profile]
