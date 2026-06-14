"""The Commentator — a modular "color commentary" observer.

Most agents are pure declarative config; the commentator needs a handler for two
things the generic turn cannot express:

  1. **Cadence.** It holds its tongue until a few public speech beats have landed
     since its last remark — a simple count (``MAL_COMMENTATOR_EVERY``, default 4),
     not a per-speaker quorum. It is polled every turn (``schedule.tick_every: 1``)
     and ABSTAINS (returns ``None``) until that many beats accrue, then delivers
     exactly one beat. A plain count means a stalled or errored speaker can never
     wedge the cadence (so the illustrated/spoken media beat always eventually fires).

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
_DEFAULT_EVERY = 1


@register_handler("commentator")
class Commentator(ManifestAgent):
    """Color commentary on a fixed-cadence beat counter, with an illustrated, spoken beat."""

    # ── cadence ───────────────────────────────────────────────────────────────

    def _every(self) -> int:
        """How many public speech beats must land before the next remark (default 4).

        A simple count, not a per-speaker quorum: a stalled or errored speaker can never
        wedge the cadence (the old quorum required *every* speaker who ever spoke to keep
        speaking, so one silent agent blocked commentary forever — and starved the media
        beat with it). A handler constant with a ``MAL_COMMENTATOR_EVERY`` env override —
        the manifest is ``extra='forbid'`` so this cannot be a YAML field yet (a typed
        CommentaryConfig sub-schema is the clean follow-up). Floored at 1 so a bad value
        can't wedge it."""
        try:
            return max(1, int(os.getenv("MAL_COMMENTATOR_EVERY", str(_DEFAULT_EVERY))))
        except ValueError:
            return _DEFAULT_EVERY

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
        return self._beats_since_last(events) >= self._every()

    # ── prompt steering ─────────────────────────────────────────────────────────

    def _build_extra_prompt(self, projection: StageProjection, recent_events: tuple[Event, ...]) -> str:
        """Steer the model toward a short, funny review of the beat (not narration)."""
        return (
            "YOUR JOB\n"
            "Deliver ONE punchy, funny review of the beat above — one or two sentences, "
            "like a heckle from the cheap seats. Be specific to what the cast just did. "
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
