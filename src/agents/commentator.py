"""The Commentator — a modular "color commentary" observer.

Most agents are pure declarative config; the commentator needs a handler for two
things the generic turn cannot express:

  1. **Cadence.** It holds its tongue until *each* active cast member has spoken a
     few times since its last remark — a per-speaker quorum, not a fixed turn count.
     It is polled every turn (``schedule.tick_every: 1``) and ABSTAINS (returns
     ``None``) until the quorum is met, then delivers exactly one beat.

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
_DEFAULT_PER_AGENT = 3


@register_handler("commentator")
class Commentator(ManifestAgent):
    """Color commentary on a quorum cadence, with an illustrated, spoken beat."""

    # ── cadence ───────────────────────────────────────────────────────────────

    def _per_agent(self) -> int:
        """How many times each active speaker must speak before the next remark.

        A handler constant with a ``MAL_COMMENTATOR_EVERY`` env override — the manifest
        is ``extra='forbid'`` so this cannot be a YAML field yet (a typed CommentaryConfig
        sub-schema is the clean follow-up). Floored at 1 so a bad value can't wedge it."""
        try:
            return max(1, int(os.getenv("MAL_COMMENTATOR_EVERY", str(_DEFAULT_PER_AGENT))))
        except ValueError:
            return _DEFAULT_PER_AGENT

    def _window_since_last(self, events: tuple[Event, ...]) -> tuple[Event, ...]:
        """Events after this agent's most recent remark — its quorum resets each beat."""
        last = -1
        for i, event in enumerate(events):
            if event.kind == _COMMENTARY_KIND and event.actor == self.name:
                last = i
        return events[last + 1 :]

    def _active_speakers(self, events: tuple[Event, ...]) -> set[str]:
        """Cast members (never self) who have spoken at least once this run.

        Judges — whose verdicts are not speech kinds — and the commentator itself fall
        out naturally, so the quorum tracks only the talking cast."""
        cast = set(self.cast_names)
        return {e.actor for e in events if e.kind in _SPEECH_KINDS and e.actor in cast and e.actor != self.name}

    def _quorum_met(self, events: tuple[Event, ...]) -> bool:
        """True once every active speaker has spoken ``_per_agent`` times since the last beat."""
        speakers = self._active_speakers(events)
        if not speakers:
            return False
        need = self._per_agent()
        counts = dict.fromkeys(speakers, 0)
        for event in self._window_since_last(events):
            if event.kind in _SPEECH_KINDS and event.actor in counts:
                counts[event.actor] += 1
        return all(counts[name] >= need for name in speakers)

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
        # Hold until each active speaker has spoken enough since the last remark.
        if not self._quorum_met(recent_events):
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
