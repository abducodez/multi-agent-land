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

import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from src import observability as obs
from src.core.context import ContextBuilder
from src.core.events import Event
from src.core.manifest import AgentManifest
from src.core.memory import EpisodicMemory, ReflectionTracker, SalienceMemory
from src.core.projections import StageProjection
from src.core.structured import (
    AgentOutputError,
    build_output_model,
    clean_clue,
    extract_reasoning,
    is_usable_line,
    json_instruction,
    parse_agent_output,
)
from src.models.provider import is_model_error
from src.models.router import ModelRouter

if TYPE_CHECKING:
    from src.core.memory_index import MemoryIndex
    from src.tools.registry import ToolRegistry

_ctx = ContextBuilder()

# System-level memory event every reflecting agent may emit, independent of its
# domain `may_emit` grant — reflection compacts memory, it is not a world action.
_REFLECTION_KIND = "agent.reflected"

# Live fallback when structured output fails: ask for a plain spoken line, NOT JSON.
# Weak/reasoning models echo a JSON schema (and its example) and leak their reasoning;
# asking for prose gives a clean line we can strip and ship.
_PROSE_FALLBACK = (
    "\n\nNow say your line aloud — one or two vivid, in-character sentences and nothing else. "
    "No JSON, no labels, no analysis, no quotation marks. "
    "Never name or spell the secret word you were given; only describe it."
)

# Kinds we de-duplicate so the cast advances the tale instead of echoing the same line
# (small models ignore "never repeat"; this enforces it).  `world.observed` is included:
# the seedkeeper narrates the world every turn, so without it a weak model loops on the
# same scene line (and other agents parrot it back).  Verdicts (the closing ruling) and
# reflections (private memory compaction) are excluded — they are not table chatter.
_SPEECH_KINDS = frozenset({"agent.spoke", "oracle.spoke", "agent.thought", "world.observed"})
_WORD = re.compile(r"[a-z0-9']+")


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

    def __init__(
        self,
        router: ModelRouter,
        tools: "ToolRegistry | None" = None,
        memory_index: "MemoryIndex | None" = None,
    ) -> None:
        self.router = router
        self.tools = tools
        # Optional semantic relevance index — a derived, rebuildable lens over the
        # ledger (ADR-0018).  ``None`` (offline default) keeps salience on the
        # keyword path.
        self.memory_index = memory_index
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
        extra_fields = self.manifest.output_extra_fields or None
        base_prompt = "\n".join(filter(None, [context, extra, tools_block]))
        # DEBUG: the FULL prompt this agent will send to its model (a key user ask).
        obs.log("agent.prompt", level="debug", agent=self.manifest.name, allowed=allowed, prompt=base_prompt)

        parsed = self._resolve_payload(self.manifest.name, base_prompt, allowed, extra_fields)

        # Don't echo the table: if this line near-duplicates a recent spoken one, skip
        # the turn (the conductor records it and moves on) so the conversation advances.
        # Live only — the offline stub's curated catalogue is reproducible by design, and
        # de-duplicating its small set of lines would starve demos and tests of events.
        if (
            not getattr(self.router, "offline", False)
            and parsed.get("kind") in _SPEECH_KINDS
            and self._is_repeat(parsed.get("text", ""), recent_events)
        ):
            obs.log("agent.repeat_skip", agent=self.manifest.name, text=str(parsed.get("text", ""))[:120])
            raise AgentOutputError(f"{self.manifest.name}: repeated a recent line — skipped to keep it moving")

        obs.log("agent.acted", agent=self.manifest.name, kind=parsed["kind"], text=str(parsed.get("text", ""))[:160])
        return Event(
            run_id=run_id,
            turn=turn,
            kind=parsed["kind"],
            actor=self.manifest.name,
            payload={k: v for k, v in parsed.items() if k != "kind"},
        )

    @staticmethod
    def _is_repeat(text: str, recent_events: tuple[Event, ...], *, look_back: int = 12, threshold: float = 0.8) -> bool:
        """True when *text* echoes a recent spoken line — exact (token set) or high overlap.

        Enforces the "say something new" rule small models ignore: peers' and the agent's
        own recent lines are compared by token-set Jaccard, so a verbatim or near-verbatim
        repeat is caught and skipped, keeping the conversation moving instead of looping."""
        tokens = set(_WORD.findall((text or "").lower()))
        if not tokens:
            return False
        spoken = [e for e in recent_events if e.kind in _SPEECH_KINDS][-look_back:]
        for event in spoken:
            prior = set(_WORD.findall(str(event.payload.get("text", "")).lower()))
            if prior and len(tokens & prior) / len(tokens | prior) >= threshold:
                return True
        return False

    def _build_extra_prompt(
        self,
        projection: StageProjection,
        recent_events: tuple[Event, ...],
    ) -> str:
        """Override to inject scenario-specific instructions."""
        return ""

    # ── model routing ─────────────────────────────────────────────────────────

    @property
    def _route_key(self) -> str:
        """Router key for this agent: the explicit ``model_endpoint`` catalogue key
        when set (a specific served model), else the logical ``model_profile`` tier.

        The router accepts either — a catalogue key resolves to that model's live
        binding, a tier to the profile default — so an agent can be pinned to one
        concrete Modal model without the engine naming a model anywhere (ADR-0022)."""
        return self.manifest.model_endpoint or self.manifest.model_profile

    def _resolve_payload(
        self,
        role: str,
        prompt: str,
        allowed: list[str],
        extra_fields: list[str] | None,
    ) -> dict:
        """Produce a validated ``{kind, text, …}`` payload for *role*.

        Live path: ask the provider for a Pydantic model whose ``kind`` is
        constrained to *allowed* — validated by construction.  When that fails (a
        small or reasoning model that won't emit clean JSON), DON'T re-prompt with
        the schema: weak models echo the instruction, copy the example, and leak
        their reasoning (and the secret word) into the line.  Instead ask for a
        PLAIN-PROSE line, strip the thinking, and — if nothing usable survives —
        raise so the conductor skips the turn rather than ship ``…`` or junk.

        Offline path (deterministic stub, no ``complete_structured``): append the
        JSON instruction and run the tolerant parser as before.  Token/cost usage
        is recorded from the provider in every path.
        """
        wants_thought = bool(extra_fields and "thought" in extra_fields)
        provider = self.router.for_profile(self._route_key)
        with obs.span("agent.resolve", **{"mal.agent": role, "mal.profile": self._route_key}):
            if hasattr(provider, "complete_structured"):
                model = build_output_model(allowed, extra_fields)
                try:
                    result = provider.complete_structured(role, prompt, model)
                    self.last_usage = dict(provider.last_usage)
                    payload = self._with_reasoning(result.model_dump(), provider, "", wants_thought)
                    if is_usable_line(payload.get("text", "")):
                        obs.add_span_attrs(**{"resolve.path": "structured", "event.kind": payload.get("kind", "")})
                        return payload
                except Exception:
                    pass  # structured failed — fall through to the prose fallback
                obs.add_span_attrs(**{"resolve.path": "prose_fallback"})
                return self._prose_fallback(role, prompt, allowed, wants_thought, provider)

            instruction = json_instruction(allowed, extra_fields=extra_fields)
            raw = provider.complete(role, f"{prompt}\n{instruction}")
            self.last_usage = dict(provider.last_usage)
            self._guard_model_error(role, raw)
            parsed = parse_agent_output(raw, allowed_kinds=allowed, fallback_kind=allowed[0])
            obs.add_span_attrs(**{"resolve.path": "offline_parse", "event.kind": parsed.get("kind", "")})
            return self._with_reasoning(parsed, provider, raw, wants_thought)

    def _guard_model_error(self, role: str, raw: str) -> None:
        """Raise when *raw* is a provider failure sentinel, not a spoken line.

        ``complete()`` returns the ``[model error: …]`` sentinel instead of raising when
        a model call fails (a transient connection drop, a 5xx).  Turning it back into an
        exception here hands the failure to the conductor's resilient loop, which skips
        this agent's turn and records it in ``agent_errors`` — so the error never reaches
        the stage as the agent's line (ADR-0023)."""
        if is_model_error(raw):
            raise AgentOutputError(f"{getattr(self, 'name', role)}: model call failed — {raw}")

    def _prose_fallback(self, role, prompt, allowed, wants_thought, provider) -> dict:
        """Re-prompt for a plain spoken line and clean it; skip the turn if it's junk."""
        obs.log("agent.prose_fallback", agent=getattr(self, "name", role))
        raw = provider.complete(role, prompt + _PROSE_FALLBACK)
        self.last_usage = dict(provider.last_usage)
        self._guard_model_error(role, raw)
        clue, residue = clean_clue(raw)
        if not is_usable_line(clue):
            raise AgentOutputError(f"{getattr(self, 'name', role)}: no usable line from prose fallback")
        payload: dict = {"kind": allowed[0], "text": clue}
        if wants_thought:
            thought = (getattr(provider, "last_reasoning", "") or "").strip() or extract_reasoning(raw) or residue
            if thought:
                payload["thought"] = thought[:600]
        return payload

    @staticmethod
    def _with_reasoning(payload: dict, provider, raw: str, wants_thought: bool) -> dict:
        """Surface the model's *thinking* as the ``thought`` when it gave none.

        Reasoning models return their chain-of-thought separately
        (``provider.last_reasoning``, from vLLM's reasoning parser) or inline in
        ``<think>`` tags.  When the agent wants a ``thought`` and the structured
        field was empty (the fallback path), we fill it from that reasoning so the
        UI's mind-reader has something real to show.  It rides only on this event's
        payload — the blackboard and memory share ``text`` alone, so a peer never
        reads another mind's thinking."""
        if not wants_thought or payload.get("thought"):
            return payload
        reasoning = (getattr(provider, "last_reasoning", "") or "").strip() or extract_reasoning(raw)
        if reasoning:
            payload["thought"] = reasoning
        return payload

    def _complete(self, role: str, prompt: str) -> str:
        """Route to the provider for this agent's profile and record token usage."""
        provider = self.router.for_profile(self._route_key)
        raw = provider.complete(role, prompt)
        self.last_usage = dict(provider.last_usage)
        self._guard_model_error(role, raw)
        return raw

    # ── memory ──────────────────────────────────────────────────────────────

    def _recall(self, turn: int, projection: StageProjection, recent_events: tuple[Event, ...]) -> str:
        cfg = self.manifest.memory
        if cfg.use_salience:
            # The index (when attached) upgrades only the relevance term to
            # semantic search; recency/importance and the visibility filter are
            # unchanged.  With no index this is the keyword-Jaccard path.
            return SalienceMemory(
                self.manifest.name, top_k=cfg.salience_top_k, index=self.memory_index
            ).format_for_prompt(recent_events, current_turn=turn, query=projection.current_scene)
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
            "OUTPUT FORMAT\nReply with a single JSON object and nothing else: "
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
