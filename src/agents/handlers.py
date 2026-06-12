"""Custom agent behaviour handlers.

Most agents are pure declarative config (a YAML manifest + the generic
ManifestAgent).  An agent only needs a handler here when it does something the
generic turn can't express — most often, calling a tool.  Handlers register
themselves with :func:`register_handler` and are referenced by ``handler:`` in a
manifest; the manifest still supplies all declarative fields.
"""

from __future__ import annotations

import re

from src.agents.base import ManifestAgent
from src.core.events import Event
from src.core.projections import StageProjection
from src.core.registry import register_handler


@register_handler("spy-host")
class SpyHost(ManifestAgent):
    """The word-pair bluff host: delivers a verdict, scores the game, unmasks every word.

    The generic turn produces the verdict *text* (who the host accuses).  This handler
    then does two things on the emitted ``judge.verdict`` payload:

      * **Scoreboard (W2.2).**  Ground truth lives in code, not the model: it parses the
        accused name out of the prose, compares it to the *actual* spy, and stamps
        ``winner = "herd"`` (caught) or ``"spy"`` (escaped) plus ``correct: bool``.  The
        ``winner`` is a *team* label (matching ``competition.teams`` in the scenario);
        ``FishbowlSession.finalize`` reconciles a single-member team to its model.
      * **Reveal.**  One ``{agent, secret, role}`` row per on-stage player — exactly the
        shape the Fishbowl verdict banner renders (``view_model``/``render_verdict``).

    Both ride the real ledger, so the unmasking and the score are genuine engine events,
    not a UI overlay.  The secret-word map is curated demo content for ``the-steeped``
    (mirroring the words baked into each player's persona); only players actually present
    on stage are revealed, so editing the cast in the Lab never produces a phantom row.
    """

    # agent name → (secret word, table role) for the shipped "the-steeped" cast.
    _REVEAL: dict[str, tuple[str, str]] = {
        "spy-cara": ("COFFEE", "HERD"),
        "spy-bex": ("COFFEE", "HERD"),
        "spy-ovo": ("COFFEE", "HERD"),
        "spy-nil": ("TEA", "SPY — CAUGHT"),
    }

    @property
    def _true_spy(self) -> str | None:
        """The agent who actually holds the odd word — the ground truth the prose is scored against."""
        return next((name for name, (_, role) in self._REVEAL.items() if "SPY" in role), None)

    def act(
        self,
        run_id: str,
        turn: int,
        projection: StageProjection,
        recent_events: tuple[Event, ...],
    ) -> Event:
        event = super().act(run_id, turn, projection, recent_events)
        on_stage = {e.actor for e in recent_events}
        present = [name for name in self._REVEAL if name in on_stage]
        if present:
            event.payload["reveal"] = [
                {"agent": name, "secret": self._REVEAL[name][0], "role": self._REVEAL[name][1]} for name in present
            ]
            accused = self._accused(event.payload.get("text", ""), present)
            correct = accused is not None and accused == self._true_spy
            event.payload["correct"] = correct
            event.payload["winner"] = "herd" if correct else "spy"
        return event

    @staticmethod
    def _accused(text: str, present: list[str]) -> str | None:
        """Which on-stage player the host's prose names as the spy.

        Personas/verdicts name players by their bare handle in caps ("NIL is the spy"),
        so match each present agent's tail segment (``spy-nil`` → ``NIL``) as a whole word."""
        words = set(re.findall(r"[a-z]+", text.lower()))
        for name in present:
            if name.rsplit("-", 1)[-1].lower() in words:
                return name
        return None


@register_handler("fortune-teller")
class FortuneTeller(ManifestAgent):
    """Draws an omen from the ``oracle`` tool and weaves it into its prophecy.

    Demonstrates the full tool path: capability-checked call → result injected
    into the prompt → result also recorded on the emitted event's payload.
    """

    def _build_extra_prompt(self, projection: StageProjection, recent_events: tuple[Event, ...]) -> str:
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
