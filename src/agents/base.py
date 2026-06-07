"""Agent base protocol and manifest-driven agent base class.

Two layers:

  Agent (ABC) — the minimal interface the conductor requires.  Any object
  with an ``act()`` method and a ``name`` is a valid agent.  Kept minimal so
  simple stubs and deterministic test agents stay trivial.

  ManifestAgent — extends Agent with a manifest, per-profile model routing,
  layered memory (episodic / salience), reflection, structured output, and
  capability-checked tools.  A manifest + this base is usually all a new agent
  needs; only special behaviour requires a subclass (override
  ``_build_extra_prompt``).  This is the workhorse of the modular system.

Backward compatibility: Phase-0/1 agents extend Agent directly and are
unaffected.  The conductor checks ``getattr(agent, "manifest", None)`` to
decide whether manifest-based routing applies.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from src.core.context import ContextBuilder
from src.core.events import Event
from src.core.manifest import AgentManifest
from src.core.memory import EpisodicMemory, ReflectionTracker, SalienceMemory
from src.core.projections import StageProjection
from src.core.structured import json_instruction, parse_agent_output
from src.models.router import ModelRouter

if TYPE_CHECKING:
    from src.tools.registry import ToolRegistry

_ctx = ContextBuilder()

# System-level memory event every reflecting agent may emit, independent of its
# domain `may_emit` grant — reflection compacts memory, it is not a world action.
_REFLECTION_KIND = "agent.reflected"


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

    Subclasses provide a class-level ``manifest``.  Construction takes a
    :class:`ModelRouter` (per-profile model selection) and an optional
    :class:`ToolRegistry` (capability-checked tools).  ``act()`` handles context
    assembly, memory, reflection, model routing, and structured output, so most
    agents need no extra code.
    """

    manifest: AgentManifest

    def __init__(self, router: ModelRouter, tools: "ToolRegistry | None" = None) -> None:
        self.router = router
        self.tools = tools
        self._reflection_tracker: ReflectionTracker | None = None
        self.last_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    @property
    def name(self) -> str:  # type: ignore[override]
        return self.manifest.name

    # ── main turn ───────────────────────────────────────────────────────────

    def act(
        self,
        run_id: str,
        turn: int,
        projection: StageProjection,
        recent_events: tuple[Event, ...],
    ) -> Event:
        mem_cfg = self.manifest.memory

        # Reflection takes priority on its scheduled turn: compact memory into a
        # belief instead of acting on the world this turn.
        threshold = mem_cfg.reflection_threshold
        if threshold is not None and self._tracker(threshold).observe(recent_events):
            return self._emit_reflection(run_id, turn, recent_events)

        memory_text = self._recall(turn, projection, recent_events)
        context = _ctx.build(
            agent_name=self.manifest.name,
            persona=self.manifest.persona,
            projection=projection,
            all_events=recent_events,
            memory_window=mem_cfg.window,
            memory_text=memory_text,  # FIX: salience/episodic recall is now actually used
        )
        extra = self._build_extra_prompt(projection, recent_events)
        tools_block = self._tools_block()
        allowed = self._content_kinds()
        instruction = json_instruction(allowed, extra_fields=self.manifest.output_extra_fields or None)
        full_prompt = "\n".join(filter(None, [context, extra, tools_block, instruction]))

        raw = self._complete(self.manifest.name, full_prompt)
        parsed = parse_agent_output(raw, allowed_kinds=allowed, fallback_kind=allowed[0])

        return Event(
            run_id=run_id,
            turn=turn,
            kind=parsed["kind"],
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

    # ── model routing ─────────────────────────────────────────────────────────

    def _complete(self, role: str, prompt: str) -> str:
        """Route to the provider for this agent's profile and record token usage."""
        provider = self.router.for_profile(self.manifest.model_profile)
        raw = provider.complete(role, prompt)
        self.last_usage = dict(provider.last_usage)
        return raw

    # ── memory ──────────────────────────────────────────────────────────────

    def _recall(self, turn: int, projection: StageProjection, recent_events: tuple[Event, ...]) -> str:
        cfg = self.manifest.memory
        if cfg.use_salience:
            return SalienceMemory(self.manifest.name, top_k=cfg.salience_top_k).format_for_prompt(
                recent_events, current_turn=turn, query=projection.current_scene
            )
        return EpisodicMemory(self.manifest.name, max_recent=cfg.window).format_for_prompt(recent_events)

    def _tracker(self, threshold: int) -> ReflectionTracker:
        if self._reflection_tracker is None:
            self._reflection_tracker = ReflectionTracker(self.manifest.name, threshold)
        return self._reflection_tracker

    def _emit_reflection(self, run_id: str, turn: int, recent_events: tuple[Event, ...]) -> Event:
        memory = EpisodicMemory(self.manifest.name, max_recent=20).format_for_prompt(recent_events)
        prompt = (
            f"IDENTITY\n{self.manifest.persona}\n\n"
            f"RECENT MEMORY (events you witnessed)\n{memory}\n\n"
            "TASK\nSynthesise the above into ONE short, high-level belief about yourself or the "
            "world. It will replace raw memories in your future context.\n\n"
            'OUTPUT FORMAT\nReply with a single JSON object and nothing else: '
            '{"kind": "agent.reflected", "text": "<one-sentence belief>"}'
        )
        raw = self._complete(self.manifest.name + "-reflect", prompt)
        parsed = parse_agent_output(raw, [_REFLECTION_KIND], _REFLECTION_KIND)
        return Event(
            run_id=run_id,
            turn=turn,
            kind=_REFLECTION_KIND,
            actor=self.manifest.name,
            payload={k: v for k, v in parsed.items() if k != "kind"},
        )

    # ── output authority ──────────────────────────────────────────────────────

    def _content_kinds(self) -> list[str]:
        """Domain kinds this agent may emit on a normal turn (excludes reflection)."""
        kinds = [k for k in self.manifest.may_emit if k != _REFLECTION_KIND]
        return kinds or ["agent.spoke"]

    # ── tools ───────────────────────────────────────────────────────────────

    def _tools_block(self) -> str:
        if self.tools is None or not self.manifest.tools:
            return ""
        described = self.tools.describe(self.manifest.tools)
        return f"AVAILABLE TOOLS\n{described}" if described else ""

    def call_tool(self, tool: str, **params) -> dict:
        """Capability-checked tool call.  Raises if the manifest does not grant *tool*."""
        if self.tools is None:
            raise RuntimeError(f"{self.manifest.name} has no tool registry attached")
        return self.tools.call(self.manifest.name, self.manifest, tool, params)
