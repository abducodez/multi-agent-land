"""Twenty Sprouts handlers — a 20-questions game where code owns the ground truth.

The secret word is *dealt by code*, never by the model — exactly the discipline The
Steeped uses for the spy words.  Two handlers:

  * :class:`SecretKeeper` deals a secret word deterministically from the seed and
    carries it on its events as a **private** ``secret`` payload key.  Because the
    context/memory builder only ever surfaces an event's ``text`` (``_displayable``
    in ``src/core/memory.py``), the guesser never sees the word — only the keeper's
    yes/no answers.  The word rides the ledger as ground truth without leaking.
  * :class:`SproutJudge` (a :class:`~src.agents.competition.JudgedCompetition`) reads
    the dealt word off the ledger and the guesser's last line, decides the winner in
    code (the guess contains the word → guesser; else the keeper kept its secret),
    and attaches a ``reveal`` unmasking the word.  Deterministic win condition,
    reproducible offline.
"""

from __future__ import annotations

import hashlib
import re

from src.agents.base import ManifestAgent
from src.agents.competition import JudgedCompetition
from src.core.events import Event
from src.core.projections import StageProjection
from src.core.registry import register_handler

# Curated, woodland-flavoured words the keeper can hold.  Evocative enough to make a
# clean offline demo, concrete enough that a guesser can corner them with yes/no.
_WORDS: tuple[str, ...] = (
    "ACORN",
    "LANTERN",
    "RIVER",
    "FIDDLE",
    "COMPASS",
    "EMBER",
    "WILLOW",
    "KETTLE",
    "FEATHER",
    "BRIDGE",
)

_GUESSER_NAME = "sprout-guesser"
_WORD = re.compile(r"[a-z]+")


def _word_for_seed(seed: str) -> str:
    """Deal a secret word as a pure function of the seed — reproducible offline."""
    digest = hashlib.sha256((seed or "").encode("utf-8")).hexdigest()
    return _WORDS[int(digest[:8], 16) % len(_WORDS)]


@register_handler("secret-keeper")
class SecretKeeper(ManifestAgent):
    """Holds the dealt word and answers the guesser, never spelling it aloud.

    The word is stamped on every one of the keeper's events as a private ``secret``
    key — visible to the judge (which reads payloads off the ledger) but never to the
    guesser (whose context is built from ``text`` only).  The keeper's spoken ``text``
    is its yes/no answer; the secret stays out of it.
    """

    def _build_extra_prompt(self, projection: StageProjection, recent_events: tuple[Event, ...]) -> str:
        word = _word_for_seed(projection.seed)
        return (
            f"YOUR SECRET WORD (never write, spell, or quote it — only answer about it): {word}\n"
            "Answer the guesser's most recent yes/no question truthfully in one short sentence. "
            "If they have not asked yet, invite them to begin. Never reveal the word."
        )

    def act(
        self,
        run_id: str,
        turn: int,
        projection: StageProjection,
        recent_events: tuple[Event, ...],
    ) -> Event:
        event = super().act(run_id, turn, projection, recent_events)
        # Ground truth on the ledger, private (non-``text``) so it never reaches the
        # guesser's prompt — the judge reads it back at the reckoning.
        event.payload["secret"] = _word_for_seed(projection.seed)
        return event


@register_handler("sprout-judge")
class SproutJudge(JudgedCompetition):
    """Decides Twenty Sprouts in code: did the guesser's last line contain the word?

    Reads the dealt word off the keeper's latest event and the guesser's most recent
    line, both from the ledger.  Winner is the **agent name** (``sprout-guesser`` if
    the word appears in their guess, else ``secret-keeper``), so the run's
    winner→model attribution maps straight through the cast.  Attaches a ``reveal``
    unmasking the word for the verdict banner.
    """

    def decide_winner(
        self,
        event: Event,
        candidates: list[str],
        recent_events: tuple[Event, ...],
    ) -> str | None:
        secret = self._dealt_word(recent_events)
        if not secret:
            return super().decide_winner(event, candidates, recent_events)
        guess = self._last_guess(recent_events)
        caught = secret.lower() in set(_WORD.findall(guess.lower()))
        event.payload["correct"] = caught
        event.payload["reveal"] = [
            {
                "agent": "secret-keeper",
                "secret": secret,
                "role": "GUESSED" if caught else "KEPT SECRET",
            }
        ]
        return _GUESSER_NAME if caught else "secret-keeper"

    @staticmethod
    def _dealt_word(recent_events: tuple[Event, ...]) -> str:
        for e in reversed(recent_events):
            secret = e.payload.get("secret")
            if secret:
                return str(secret)
        return ""

    @staticmethod
    def _last_guess(recent_events: tuple[Event, ...]) -> str:
        for e in reversed(recent_events):
            if e.actor == _GUESSER_NAME and e.kind == "agent.spoke":
                return str(e.payload.get("text", ""))
        return ""
