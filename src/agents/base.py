"""Agent base protocol and manifest-driven agent base class.

Two layers:

  Agent (ABC) — the minimal interface the conductor requires.  Any object
  with an `act()` method and a `name` is a valid agent.  Kept minimal so
  that simple stub agents and deterministic test agents stay trivial.

  ManifestAgent (abstract subclass) — extends Agent with a manifest,
  structured output, and the context-builder integration.  New agents in
  Phases 2+ should extend this class.

Backward compatibility: Phase 0/1 agents extend Agent directly and work
without changes.  The conductor checks `hasattr(agent, "manifest")` to
decide whether manifest-based routing applies.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.context import ContextBuilder
from src.core.events import Event
from src.core.manifest import AgentManifest
from src.core.projections import StageProjection
from src.core.structured import json_instruction, parse_agent_output
from src.models.provider import ModelProvider

_ctx = ContextBuilder()


# ── minimal interface ─────────────────────────────────────────────────────────

class Agent(ABC):
    name: str

    @abstractmethod
    def act(
        self,
        run_id: str,
        turn: int,
        projection: StageProjection,
        recent_events: tuple[Event, ...],
    ) -> Event:
        raise NotImplementedError


# ── manifest-driven base ──────────────────────────────────────────────────────

class ManifestAgent(Agent):
    """Base class for manifest-driven agents.

    Subclasses only need to provide:
      - manifest: AgentManifest  (class-level attribute)
      - model: ModelProvider     (constructor argument)

    The act() implementation here handles context assembly, structured output
    parsing, and event construction.  Override _build_extra_prompt() to inject
    scenario-specific instructions between the context and the JSON instruction.
    """

    manifest: AgentManifest

    def __init__(self, model: ModelProvider) -> None:
        self.model = model

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.manifest.name

    def act(
        self,
        run_id: str,
        turn: int,
        projection: StageProjection,
        recent_events: tuple[Event, ...],
    ) -> Event:
        # Build memory-aware context
        mem_cfg = self.manifest.memory
        use_salience = mem_cfg.use_salience

        if use_salience:
            from src.core.memory import SalienceMemory
            memory_str = SalienceMemory(
                agent_name=self.manifest.name,
                top_k=mem_cfg.salience_top_k,
            ).format_for_prompt(
                events=recent_events,
                current_turn=turn,
                query=projection.current_scene,
            )
        else:
            from src.core.memory import EpisodicMemory
            memory_str = EpisodicMemory(
                agent_name=self.manifest.name,
                max_recent=mem_cfg.window,
            ).format_for_prompt(recent_events)

        context = _ctx.build(
            agent_name=self.manifest.name,
            persona=self.manifest.persona,
            projection=projection,
            all_events=recent_events,
            memory_window=mem_cfg.window,
        )

        extra = self._build_extra_prompt(projection, recent_events)
        instruction = json_instruction(self.manifest.may_emit or ["agent.spoke"])
        full_prompt = "\n".join(filter(None, [context, extra, instruction]))

        # Resolve model profile to concrete name
        from src.core.manifest import resolve_model
        model_name = resolve_model(self.manifest.model_profile)

        raw = self.model.complete(self.manifest.name, full_prompt)
        parsed = parse_agent_output(
            raw=raw,
            allowed_kinds=self.manifest.may_emit or ["agent.spoke"],
            fallback_kind=(self.manifest.may_emit or ["agent.spoke"])[0],
        )

        return Event(
            run_id=run_id,
            turn=turn,
            kind=parsed["kind"],  # type: ignore[arg-type]
            actor=self.manifest.name,
            payload={k: v for k, v in parsed.items() if k != "kind"},
        )

    def _build_extra_prompt(
        self,
        projection: StageProjection,
        recent_events: tuple[Event, ...],
    ) -> str:
        """Override to inject scenario-specific instructions."""
        return ""
