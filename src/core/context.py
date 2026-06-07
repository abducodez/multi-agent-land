from __future__ import annotations

from src.core.events import Event
from src.core.memory import EpisodicMemory
from src.core.projections import StageProjection


class ContextBuilder:
    """Assembles a compact, role-scoped prompt for a single agent turn.

    Layers, innermost first:
      1. Pinned persona  — fixed identity and constraints
      2. Current scene   — world state from the projection
      3. Memory          — episodic recall from the ledger
      4. Visitor noise   — recent user injections
    """

    def build(
        self,
        *,
        agent_name: str,
        persona: str,
        projection: StageProjection,
        all_events: tuple[Event, ...],
        memory_window: int = 8,
    ) -> str:
        memory = EpisodicMemory(agent_name, max_recent=memory_window)
        recall = memory.format_for_prompt(all_events)

        visitor_lines = "\n".join(f"- {a}" for a in projection.user_artifacts[-3:]) or "(quiet)"

        return (
            f"IDENTITY\n{persona}\n\n"
            f"CURRENT SCENE\n{projection.current_scene}\n\n"
            f"YOUR MEMORY (recent events you witnessed)\n{recall}\n\n"
            f"VISITOR DISTURBANCES\n{visitor_lines}"
        )
