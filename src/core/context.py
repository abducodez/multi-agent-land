"""Context builder — assembles a compact, role-scoped prompt per agent turn.

Layering order (innermost → outermost, smallest → largest prompt budget):

  1. IDENTITY        — pinned persona (permanent cost, never drops)
  2. SHARED GOAL     — the scenario objective (only when set; from the projection)
  3. CURRENT SCENE   — world state from the stage projection
  4. YOUR MEMORY     — episodic recall from the ledger (windowed or salience-ranked)
  5. VISITOR         — recent user injections (always salient)
  [6. EXTRA]         — injected by ManifestAgent subclass for scenario-specific context
  [7. OUTPUT FORMAT] — JSON constraint block (appended by structured.py)

The builder owns the structure.  Agents own only the persona and the action.
Changing the prompt strategy for all agents is a one-file edit here.
"""
from __future__ import annotations

from src.core.events import Event
from src.core.memory import EpisodicMemory
from src.core.projections import StageProjection


class ContextBuilder:
    """Assembles a compact, role-scoped prompt for a single agent turn."""

    def build(
        self,
        *,
        agent_name: str,
        persona: str,
        projection: StageProjection,
        all_events: tuple[Event, ...],
        memory_window: int = 8,
        memory_text: str | None = None,
    ) -> str:
        """Build a prompt string from layered context.

        Args:
            agent_name:    Used to filter visible events for memory.
            persona:       Fixed identity text (IDENTITY block).
            projection:    Current world-state view.
            all_events:    Full ledger tail for memory retrieval.
            memory_window: How many events to include (for EpisodicMemory).
            memory_text:   Pre-computed memory string (pass to override the default
                           EpisodicMemory computation, e.g. when using SalienceMemory).
        """
        if memory_text is None:
            memory_text = EpisodicMemory(agent_name, max_recent=memory_window).format_for_prompt(
                all_events
            )

        visitor_lines = (
            "\n".join(f"- {a}" for a in projection.user_artifacts[-3:]) or "(quiet)"
        )

        goal_block = f"SHARED GOAL\n{projection.goal}\n\n" if projection.goal else ""

        return (
            f"IDENTITY\n{persona}\n\n"
            f"{goal_block}"
            f"CURRENT SCENE\n{projection.current_scene}\n\n"
            f"YOUR MEMORY (recent events you witnessed)\n{memory_text}\n\n"
            f"VISITOR DISTURBANCES\n{visitor_lines}"
        )
