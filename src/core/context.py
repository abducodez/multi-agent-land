"""Context builder — assembles a compact, role-scoped prompt per agent turn.

Layering order (innermost → outermost, smallest → largest prompt budget):

  1. IDENTITY        — pinned persona (permanent cost, never drops)
  2. SHARED GOAL     — the scenario objective (only when set; from the projection)
  3. CURRENT SCENE   — world state from the stage projection
  4. THE DISCUSSION  — role-aware (ADR-0023 + follow-up):
                         · workers see WHAT'S BEEN SAID — the recent table to react to;
                         · judges see THE EXCHANGE TO JUDGE — the *complete* ordered
                           transcript, so a ruling weighs the whole discussion, not its tail.
  5. YOUR MEMORY     — episodic/salience recall (the earlier arc + world beats/verdicts;
                         deduped against block 4 so no line is printed twice)
  6. VISITOR         — recent user injections (always salient)
  [7. EXTRA]         — injected by ManifestAgent subclass for scenario-specific context
  [8. OUTPUT FORMAT] — JSON constraint block (appended by structured.py)

The builder owns the structure.  Agents own only the persona and the action.
Changing the prompt strategy for all agents is a one-file edit here.
"""

from __future__ import annotations

from src import observability as obs
from src.core.events import Event
from src.core.memory import EpisodicMemory
from src.core.projections import StageProjection

# Public speech — the lines that constitute "the discussion" everyone can hear. A private
# ``agent.thought`` is deliberately excluded: it rides only its own event (the mind-reader),
# so a judge rules on what was *said*, and peers never read another mind.
_PUBLIC_SPEECH_KINDS = frozenset({"agent.spoke", "oracle.spoke"})


class ContextBuilder:
    """Assembles a compact, role-scoped prompt for a single agent turn."""

    # How many recent peer lines a *worker's* "react now" blackboard shows. The longer arc
    # of the discussion is carried by YOUR MEMORY (recall now includes peers' spoken lines —
    # ADR-0023 follow-up), so this stays small: it's the immediate table to react to.
    _BLACKBOARD_WINDOW = 8

    # How many lines a *judge's* full transcript shows (most recent, if it overflows). Judges
    # fire infrequently and must weigh the whole exchange, so this is generous — it comfortably
    # covers every shipped scenario's discussion length.
    _TRANSCRIPT_LIMIT = 80

    def build(
        self,
        *,
        agent_name: str,
        persona: str,
        projection: StageProjection,
        all_events: tuple[Event, ...],
        memory_window: int = 8,
        memory_text: str | None = None,
        role: str = "worker",
    ) -> str:
        """Build a prompt string from layered context.

        Args:
            agent_name:    Used to filter visible events for memory.
            persona:       Fixed identity text (IDENTITY block).
            projection:    Current world-state view.
            all_events:    Full ledger tail (this run) — drives memory recall AND a judge's
                           full transcript.
            memory_window: How many events to include (for EpisodicMemory).
            memory_text:   Pre-computed memory string (pass to override the default
                           EpisodicMemory computation, e.g. when using SalienceMemory).
            role:          The agent's role. ``"judge"`` gets the complete exchange to rule
                           on; everyone else gets the recent blackboard to react to.
        """
        if memory_text is None:
            memory_text = EpisodicMemory(agent_name, max_recent=memory_window).format_for_prompt(all_events)

        # Block 4 is role-aware: a judge needs the WHOLE exchange to rule fairly; a worker
        # needs the recent table to react to. Both return the set of discussion texts they
        # already show, so YOUR MEMORY can drop those exact lines — the union an agent sees
        # is unchanged, we just never print a line twice (blackboard/transcript hold the
        # discussion, memory holds the earlier arc + world beats/verdicts).
        discussion_block, shown_texts = self._discussion(role, projection, all_events)
        shown_texts = set(shown_texts) | {(projection.current_scene or "").strip()}
        memory_text = self._dedup_memory(memory_text, shown_texts)

        visitor_lines = "\n".join(f"- {a}" for a in projection.user_artifacts[-3:]) or "(quiet)"

        goal_block = f"SHARED GOAL\n{projection.goal}\n\n" if projection.goal else ""

        # When dedup leaves nothing (common for a judge whose recall is fully covered by the
        # transcript above), show a short pointer instead of a blank or a duplicate block.
        memory_block = (
            f"YOUR MEMORY (recent events you witnessed)\n{memory_text}"
            if memory_text.strip()
            else "YOUR MEMORY\n(nothing beyond the exchange above)"
        )

        prompt = (
            f"IDENTITY\n{persona}\n\n"
            f"{goal_block}"
            f"CURRENT SCENE\n{projection.current_scene}\n\n"
            f"{discussion_block}"
            f"{memory_block}\n\n"
            f"VISITOR DISTURBANCES\n{visitor_lines}"
        )
        # Structure + size of the assembled context (the full prompt is logged by the
        # agent layer as ``agent.prompt``; here we record which sections were present).
        discussion_section = "TRANSCRIPT" if role == "judge" else "BLACKBOARD"
        sections = ["IDENTITY", "CURRENT SCENE", discussion_section, "MEMORY", "VISITOR"]
        if goal_block:
            sections.insert(1, "SHARED GOAL")
        obs.log(
            "context.build",
            level="debug",
            agent=agent_name,
            role=role,
            sections=sections,
            prompt_chars=len(prompt),
            memory_chars=len(memory_text),
        )
        return prompt

    # ── the discussion block (role-aware) ─────────────────────────────────────────

    def _discussion(
        self, role: str, projection: StageProjection, all_events: tuple[Event, ...]
    ) -> tuple[str, set[str]]:
        """The discussion block + the set of texts it shows (for memory dedup).

        Judges get the full ordered transcript (rule on everything); everyone else gets the
        recent blackboard (react to the table)."""
        if role == "judge":
            lines = self._public_lines(all_events)
            return self._transcript_block(lines), {self._note_text(line) for line in lines}
        shown_notes = [n for n in projection.agent_notes if n][-self._BLACKBOARD_WINDOW :]
        return self._blackboard_block(shown_notes), {self._note_text(n) for n in shown_notes}

    @staticmethod
    def _public_lines(all_events: tuple[Event, ...]) -> list[str]:
        """Every public spoken line in the run, oldest → newest, as ``"actor: text"``.

        This is the discussion a judge rules on. Private thoughts are excluded (see
        ``_PUBLIC_SPEECH_KINDS``); a line with no text is skipped."""
        out: list[str] = []
        for e in all_events:
            if e.kind in _PUBLIC_SPEECH_KINDS:
                text = str(e.payload.get("text", "")).strip()
                if text:
                    out.append(f"{e.actor}: {text}")
        return out

    @classmethod
    def _transcript_block(cls, lines: list[str]) -> str:
        """A judge's view: the complete exchange, in order, with a 'weigh all of it' nudge."""
        if not lines:
            return "THE EXCHANGE TO JUDGE\n(no one has spoken yet)\n\n"
        shown = lines[-cls._TRANSCRIPT_LIMIT :]
        head = (
            f"(showing the last {cls._TRANSCRIPT_LIMIT} of {len(lines)} lines)\n"
            if len(lines) > cls._TRANSCRIPT_LIMIT
            else ""
        )
        body = "\n".join(f"- {line}" for line in shown)
        return (
            "THE EXCHANGE TO JUDGE (every spoken line, in order — weigh ALL of it, not just the last few):\n"
            f"{head}{body}\n\n"
        )

    @staticmethod
    def _note_text(note: str) -> str:
        """The spoken text of a discussion line, stripped of its ``actor:`` prefix.

        Lines read ``"actor: text"``, ``"actor [kind]: text"``, or ``"💭 actor believes:
        text"`` — all carry the content after the first ``": "``. Used to match the same
        line where it appears in a memory entry (``[turn NNN][kind] text``)."""
        _, _, text = note.partition(": ")
        return (text or note).strip()

    @staticmethod
    def _dedup_memory(memory_text: str, shown_texts: set[str]) -> str:
        """Drop memory lines already shown in the discussion block or CURRENT SCENE.

        Memory lines are ``[turn NNN][kind] <text>``. The most-recent ``world.observed`` is
        also the CURRENT SCENE, and (now that peers' spoken lines are recallable) the
        discussion lines also appear in block 4. We drop any memory line ending with one of
        those already-shown texts — so the agent reads each line once: the blackboard/
        transcript holds the discussion, memory holds the earlier arc + world/verdict beats.
        Returns the empty string if every line was a duplicate — the caller then shows a
        short pointer rather than a blank or a re-print of the discussion."""
        shown = {s for s in shown_texts if s}
        if not shown:
            return memory_text
        kept = [line for line in memory_text.splitlines() if not any(line.rstrip().endswith(s) for s in shown)]
        return "\n".join(kept)

    @staticmethod
    def _blackboard_block(shown_notes: list[str]) -> str:
        """The shared blackboard: what the rest of the cast just said or did.

        Without this an agent sees only the world text and its own past lines, so a
        small model loops on the same clue and never reacts to anyone (the "shared
        blackboard isn't shared" gap — ADR-0023).  ``shown_notes`` is the already-sliced
        recent tail and carries only the public ``text`` of each peer event (never their
        private thought), so surfacing it shares the conversation without leaking minds.
        """
        notes = [n for n in shown_notes if n]
        if not notes:
            return "WHAT'S BEEN SAID\n(you go first — set the tone)\n\n"
        lines = "\n".join(f"- {n}" for n in notes)
        return (
            "WHAT'S BEEN SAID (the table so far — react to it)\n"
            f"{lines}\n"
            "Do NOT echo or rephrase any line above. Add a GENUINELY NEW angle — a different "
            "sense, detail, or suspicion — that moves the conversation forward.\n\n"
        )
