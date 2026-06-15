"""The Commentator — a universal "color commentary" observer.

It is scenario-agnostic by design: it summarises only the public ledger, so it drops
into *any* cast (a debate, a mystery, a guessing game, a living scene) with no engine
edits and no per-scenario flavour. Most agents are pure declarative config; the
commentator needs a handler for two things the generic turn cannot express:

  1. **Cadence, measured in rounds.** It holds its tongue until a configurable number
     of speaking *rounds* have passed since its last remark — where one round is
     approximated as "every known speaker has spoken once" (one beat per distinct cast
     speaker it has seen). The knob is ``commentary.rounds`` in the manifest (default 1),
     overridable at runtime via ``MAL_COMMENTATOR_ROUNDS``; the legacy
     ``MAL_COMMENTATOR_EVERY`` still pins an *absolute* beat count when set. It is polled
     every turn (``schedule.tick_every: 1``) and ABSTAINS (returns ``None``) until the
     threshold accrues, then delivers exactly one beat. The threshold is a *count* of
     beats, not a per-speaker quorum, so a stalled or errored speaker can never wedge the
     cadence (the illustrated/spoken media beat always eventually fires).

  2. **Media.** When it does speak it draws an image of the beat and says the line
     aloud, folding both onto its event — the :class:`FortuneTeller` tool pattern,
     for the ``image.render`` / ``tts.speak`` capabilities. Media is a garnish: a
     missing tool (before media is wired) or a failed call degrades the beat to text,
     never breaking the turn.

It never calls a peer and never reads another mind — it summarises only the public
ledger, exactly like every other agent. Drop ``rafters-critic`` into a scenario's
``cast`` to switch it on; remove it and the engine never knows it existed (ADR-0011).
"""

from __future__ import annotations

import os

from src import observability as obs
from src.agents.base import ManifestAgent
from src.core.events import Event
from src.core.projections import StageProjection
from src.core.registry import register_handler

# Public, ledger-visible "a cast member said something" kinds — mirrors
# ``base._SPEECH_KINDS``. The commentator's own ``commentary.posted`` is deliberately
# absent, so a remark never counts toward the next quorum (self-trigger guard #2; guard
# #1 is ``subscribes_to: []`` in the manifest, so it is never event-woken at all).
_SPEECH_KINDS = frozenset({"agent.spoke", "agent.thought", "oracle.spoke", "world.observed"})

_COMMENTARY_KIND = "commentary.posted"
_DEFAULT_ROUNDS = 1


def _env_int(name: str) -> int | None:
    """A floored-at-1 positive int from env var *name*, or None if unset/garbage."""
    raw = os.getenv(name)
    if raw is None:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


@register_handler("commentator")
class Commentator(ManifestAgent):
    """Universal color commentary on a round-paced beat counter, with an illustrated, spoken beat."""

    # ── cadence ───────────────────────────────────────────────────────────────

    def _rounds(self) -> int:
        """How many speaking rounds must pass before the next remark (default 1).

        Manifest ``commentary.rounds`` is the declared default; ``MAL_COMMENTATOR_ROUNDS``
        overrides it at runtime (the user-facing knob). Floored at 1 so a bad value can't
        wedge the cadence."""
        env = _env_int("MAL_COMMENTATOR_ROUNDS")
        if env is not None:
            return env
        cfg = self.manifest.commentary
        return max(1, cfg.rounds) if cfg else _DEFAULT_ROUNDS

    def _round_size(self, events: tuple[Event, ...]) -> int:
        """Distinct cast speakers (never self) seen so far — one round's worth of beats.

        Self-calibrating: it counts only cast members who have actually spoken, so silent
        observers and the critic itself don't inflate the round, and a scenario with three
        speakers needs three beats per round where one with five needs five."""
        cast = set(self.cast_names)
        speakers = {e.actor for e in events if e.kind in _SPEECH_KINDS and e.actor in cast and e.actor != self.name}
        return len(speakers)

    def _every(self, events: tuple[Event, ...]) -> int:
        """How many public speech beats must land before the next remark.

        Legacy ``MAL_COMMENTATOR_EVERY`` pins an *absolute* beat count when set (back-compat);
        otherwise it is ``rounds × round_size`` — "this many rounds of everyone-speaks-once".
        A plain count, not a per-speaker quorum: a stalled or errored speaker can never wedge
        the cadence (the old quorum required *every* speaker who ever spoke to keep speaking,
        so one silent agent blocked commentary forever — and starved the media beat with it).
        Floored at 1."""
        absolute = _env_int("MAL_COMMENTATOR_EVERY")
        if absolute is not None:
            return absolute
        return max(1, self._rounds() * self._round_size(events))

    def _window_since_last(self, events: tuple[Event, ...]) -> tuple[Event, ...]:
        """Events after this agent's most recent remark — its counter resets each beat."""
        last = -1
        for i, event in enumerate(events):
            if event.kind == _COMMENTARY_KIND and event.actor == self.name:
                last = i
        return events[last + 1 :]

    def _beats_since_last(self, events: tuple[Event, ...]) -> int:
        """Count cast speech beats (never self) since this critic's last remark."""
        cast = set(self.cast_names)
        return sum(
            1
            for e in self._window_since_last(events)
            if e.kind in _SPEECH_KINDS and e.actor in cast and e.actor != self.name
        )

    def _ready(self, events: tuple[Event, ...]) -> bool:
        """True once enough fresh speech has landed since the last beat to chime in."""
        return self._beats_since_last(events) >= self._every(events)

    # ── prompt steering ─────────────────────────────────────────────────────────

    def _build_extra_prompt(self, projection: StageProjection, recent_events: tuple[Event, ...]) -> str:
        """Steer the model toward a short, funny summary of the beat (not narration)."""
        return (
            "YOUR JOB\n"
            "Sum up the beat above in ONE punchy, funny line — capture what the cast just "
            "did, with affectionate wit, like a heckle from the cheap seats. Be specific. "
            "No stage directions, no quotation marks, no lists. Just the line."
        )

    # ── turn ──────────────────────────────────────────────────────────────────

    def act(
        self,
        run_id: str,
        turn: int,
        projection: StageProjection,
        recent_events: tuple[Event, ...],
    ) -> Event | None:
        # Hold until enough fresh speech beats have landed since the last remark.
        if not self._ready(recent_events):
            return None
        # The generic turn writes the funny line (offline → the curated stub keyed on
        # this agent's name); kind is constrained to ``commentary.posted`` by may_emit.
        event = super().act(run_id, turn, projection, recent_events)
        summary = str(event.payload.get("text", "")).strip()
        if not summary:
            return event

        # Draw + voice the beat. Best-effort: a missing/failed tool leaves the beat as
        # text, exactly like a media-less offline run. The slug keys the file under the
        # run so the hybrid transport can serve it (or inline a data: URI offline).
        slug = f"{turn:03d}-{event.id[:8]}"
        image = self._media_ref("image.render", prompt=summary, run_id=run_id, slug=f"{slug}-img")
        if image:
            event.payload["image"] = {"src": image["src"], "alt": summary[:120]}
        audio = self._media_ref("tts.speak", text=summary, run_id=run_id, slug=f"{slug}-tts")
        if audio:
            event.payload["audio"] = {"src": audio["src"], "mime": audio.get("mime", "")}
        return event

    def _media_ref(self, tool: str, **params) -> dict | None:
        """Best-effort media via a capability-checked tool; ``None`` on absence or failure.

        Returns the tool's ref dict (``{"src", "mime", ...}``) only when it carries a
        usable ``src``. A tool that isn't registered (before media is wired) or a failed
        generation degrades the beat to text — it must never drop the turn."""
        if self.tools is None or tool not in self.manifest.tools or not self.tools.has(tool):
            return None
        try:
            result = self.call_tool(tool, **params)
        except Exception as exc:  # noqa: BLE001 — media is garnish; a failure must not drop the beat
            obs.log("commentator.media_skip", level="warning", agent=self.name, tool=tool, error=str(exc))
            return None
        return result if (result or {}).get("src") else None
