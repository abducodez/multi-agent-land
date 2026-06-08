"""Context builder — assembles a compact, role-scoped prompt per agent turn.

Layering order (innermost → outermost, smallest → largest prompt budget):

  1. IDENTITY        — pinned persona (permanent cost, never drops)
  2. SHARED GOAL     — the scenario objective (only when set; from the projection)
  3. CURRENT SCENE   — world state from the stage projection
  4. WHAT'S BEEN SAID— the shared blackboard: peers' recent lines (ADR-0023)
  5. YOUR MEMORY     — episodic recall from the ledger (windowed or salience-ranked)
  6. VISITOR         — recent user injections (always salient)
  [7. EXTRA]         — injected by ManifestAgent subclass for scenario-specific context
  [8. OUTPUT FORMAT] — JSON constraint block (appended by structured.py)

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
            memory_text = EpisodicMemory(agent_name, max_recent=memory_window).format_for_prompt(all_events)

        visitor_lines = "\n".join(f"- {a}" for a in projection.user_artifacts[-3:]) or "(quiet)"

        goal_block = f"SHARED GOAL\n{projection.goal}\n\n" if projection.goal else ""

        return (
            f"IDENTITY\n{persona}\n\n"
            f"{goal_block}"
            f"CURRENT SCENE\n{projection.current_scene}\n\n"
            f"{self._blackboard_block(projection.agent_notes)}"
            f"YOUR MEMORY (recent events you witnessed)\n{memory_text}\n\n"
            f"VISITOR DISTURBANCES\n{visitor_lines}"
        )

    @staticmethod
    def _blackboard_block(agent_notes: list[str], window: int = 6) -> str:
        """The shared blackboard: what the rest of the cast just said or did.

        Without this an agent sees only the world text and its own past lines, so a
        small model loops on the same clue and never reacts to anyone (the "shared
        blackboard isn't shared" gap — ADR-0023).  ``agent_notes`` already carries
        only the public ``text`` of each peer event (never their private thought),
        so surfacing it shares the conversation without leaking minds.
        """
        notes = [n for n in agent_notes if n][-window:]
        if not notes:
            return "WHAT'S BEEN SAID\n(you go first — set the tone)\n\n"
        lines = "\n".join(f"- {n}" for n in notes)
        return (
            "WHAT'S BEEN SAID (the table so far — react to it)\n"
            f"{lines}\n"
            "Add a NEW line that moves things forward — never repeat one already said.\n\n"
        )
