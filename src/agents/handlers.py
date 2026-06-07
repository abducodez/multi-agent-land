"""Custom agent behaviour handlers.

Most agents are pure declarative config (a YAML manifest + the generic
ManifestAgent).  An agent only needs a handler here when it does something the
generic turn can't express — most often, calling a tool.  Handlers register
themselves with :func:`register_handler` and are referenced by ``handler:`` in a
manifest; the manifest still supplies all declarative fields.
"""
from __future__ import annotations

from src.agents.base import ManifestAgent
from src.core.events import Event
from src.core.projections import StageProjection
from src.core.registry import register_handler


@register_handler("fortune-teller")
class FortuneTeller(ManifestAgent):
    """Draws an omen from the ``oracle`` tool and weaves it into its prophecy.

    Demonstrates the full tool path: capability-checked call → result injected
    into the prompt → result also recorded on the emitted event's payload.
    """

    def _build_extra_prompt(
        self, projection: StageProjection, recent_events: tuple[Event, ...]
    ) -> str:
        self._last_omen = ""
        if self.tools is not None and "oracle" in self.manifest.tools:
            self._last_omen = self.call_tool("oracle", seed=projection.current_scene).get("omen", "")
            if self._last_omen:
                return f"AN OMEN FROM THE ORACLE\n{self._last_omen}\nWeave this omen into your prophecy."
        return ""

    def act(
        self,
        run_id: str,
        turn: int,
        projection: StageProjection,
        recent_events: tuple[Event, ...],
    ) -> Event:
        event = super().act(run_id, turn, projection, recent_events)
        omen = getattr(self, "_last_omen", "")
        if omen:
            event.payload["omen"] = omen  # tool output is visible on the ledger
        return event
