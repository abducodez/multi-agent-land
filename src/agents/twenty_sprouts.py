"""Twenty Sprouts handlers — a 20-questions game where code owns the ground truth.

The secret word is *dealt by code*, never by the model — exactly the discipline The
Steeped uses for the spy words.  Three handlers shape the round:

  * :class:`SecretKeeper` deals a secret word deterministically from the seed and
    carries it on its events as a **private** ``secret`` payload key.  Because the
    context/memory builder only ever surfaces an event's ``text`` (``_displayable``
    in ``src/core/memory.py``), the guesser never sees the word — only the keeper's
    yes/no answers.  The keeper *answers* the guesser's most recent question and never
    asks one back, and — crucially — never spells the word: the handler hands the model
    the exact question to answer, re-asks once if the reply asks a question or leaks the
    word, and scrubs the word from the spoken line as an absolute last-resort guarantee.
  * :class:`SproutGuesser` plays the asker.  It mines the ledger for every prior
    question and the keeper's answers, builds a running dossier (CONFIRMED / RULED
    OUT / ALREADY ASKED), asks ONE genuinely new question that narrows the field, and
    is forced to *commit a guess between questions* — when the facts converge, every few
    questions once it has learned enough, and as a hard stop before the round runs out —
    so it stops repeating itself and actually names the word instead of interrogating forever.
  * :class:`SproutJudge` (a :class:`~src.agents.competition.JudgedCompetition`) reads
    the dealt word off the ledger and ends the game the moment it appears in *any* guess
    (not just the latest) — the guesser wins; if the word is never named, the keeper wins
    at the timeout. It subscribes to ``agent.spoke`` so the win is called immediately, and
    attaches a ``reveal`` unmasking the word.  Deterministic win condition, reproducible offline.

The word also rides into the *view model* (``src/ui/fishbowl/view_model.py``) so the
human audience can watch the keeper hold it — visible on stage, never in any agent's
prompt.
"""

from __future__ import annotations

import hashlib
import re

from src import observability as obs
from src.agents.base import ManifestAgent
from src.agents.competition import JudgedCompetition
from src.core.events import Event
from src.core.projections import StageProjection
from src.core.registry import register_handler
from src.core.structured import AgentOutputError

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
_KEEPER_NAME = "secret-keeper"
_WORD = re.compile(r"[a-z]+")

# A keeper's reply is an *answer*, never a question.  The cheapest reliable tell that a
# small model slipped back into asking is a line that ends on a question mark — we reject
# those and re-ask (see :meth:`SecretKeeper.act`).
_ENDS_QUESTION = re.compile(r"\?\s*$")
# Lead-word classification of a yes/no answer, used to sort the guesser's dossier into
# CONFIRMED vs RULED OUT.  Anything else is a flavour/hint line and stays unsorted.
_YES_LEAD = re.compile(r"^\W*(yes|yep|yeah|yup|indeed|aye|mostly|sometimes|often|usually|kind of|sort of)\b", re.I)
_NO_LEAD = re.compile(r"^\W*(no|nope|nah|nay|never|not\b|hardly|rarely)\b", re.I)
# A final guess looks like "My guess is: LANTERN" — recognised so the dossier lists it as
# a committed guess (don't repeat it) rather than as another open question.
_FINAL_GUESS = re.compile(r"\bmy guess is\b", re.I)


def _word_for_seed(seed: str) -> str:
    """Deal a secret word as a pure function of the seed — reproducible offline."""
    digest = hashlib.sha256((seed or "").encode("utf-8")).hexdigest()
    return _WORDS[int(digest[:8], 16) % len(_WORDS)]


def _word_re(word: str) -> re.Pattern[str]:
    """Whole-word matcher for a secret word and its commonest inflections.

    Catches the bare word plus simple plural/verb endings (EMBER → embers, embered…) so the
    keeper's spoken line can be checked — and, if it leaks, scrubbed — without tripping on a
    word that merely *contains* the secret as a substring (e.g. RIVER inside "rivers" yes,
    but not "driver")."""
    return re.compile(rf"\b{re.escape(word)}(?:s|es|ed|ing)?\b", re.I)


def _contains_word(text: str, word: str) -> bool:
    """True when the keeper's line spells the secret word out (any case/inflection)."""
    return bool(word) and bool(_word_re(word).search(text or ""))


def _redact_word(text: str, word: str) -> str:
    """Mask any spelled-out secret word so it can never reach the stage.

    The guarantee of last resort: even if the model ignores every instruction and writes the
    word, this replaces it with the neutral noun ``thing`` before the line is shown, so the
    audience-facing feed (and the guesser's blackboard) never carry the answer — and the
    masked sentence still reads ("a glowing thing can emerge"). A no-op when the word is
    absent, which is always the case on the deterministic offline path."""
    return re.sub(r"\s{2,}", " ", _word_re(word).sub("thing", text or "")).strip()


def _answer_violation(text: str, word: str) -> str | None:
    """Why a keeper reply is unacceptable, for a corrective re-ask — or ``None`` if it's fine.

    Two ways a keeper stops being an *answerer*: it spells the secret word, or it asks a
    question instead of answering one."""
    if _contains_word(text, word):
        return "you spelled out the secret word — never write or hint its letters"
    if _ENDS_QUESTION.search(text or ""):
        return "you asked a question instead of answering — you must only answer"
    return None


def _trim(text: str, limit: int = 120) -> str:
    """One-line, length-bounded rendering of a ledger line for a dossier bullet."""
    line = " ".join(str(text).split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _classify_answer(text: str) -> str:
    """Sort a keeper answer into ``"yes"`` / ``"no"`` / ``"hint"`` by its lead word."""
    if _YES_LEAD.match(text or ""):
        return "yes"
    if _NO_LEAD.match(text or ""):
        return "no"
    return "hint"


def _qa_history(recent_events: tuple[Event, ...]) -> list[tuple[str, str | None]]:
    """Pair each guesser line with the keeper answer that followed it, in order.

    Walks the run's spoken events once: a guesser ``agent.spoke`` opens a new
    ``(question, None)`` pair; the next keeper ``agent.spoke`` fills its answer.  The
    result is the spine of the guesser's dossier and of the keeper's "what was just
    asked" — both read straight off the shared ledger, no side state.
    """
    pairs: list[tuple[str, str | None]] = []
    for e in recent_events:
        if e.kind != "agent.spoke":
            continue
        text = str(e.payload.get("text", "")).strip()
        if not text:
            continue
        if e.actor == _GUESSER_NAME:
            pairs.append((text, None))
        elif e.actor == _KEEPER_NAME and pairs and pairs[-1][1] is None:
            q, _ = pairs[-1]
            pairs[-1] = (q, text)
    return pairs


def _questions_since_guess(pairs: list[tuple[str, str | None]]) -> int:
    """How many questions the guesser has asked since its last committed guess.

    Counts guesser lines from the end back to the most recent ``My guess is: …`` (or the
    start if it has never guessed). Drives the "guess at least every N questions" cadence so
    the guesser commits between questions instead of interrogating forever."""
    count = 0
    for q, _ in reversed(pairs):
        if _FINAL_GUESS.search(q):
            break
        count += 1
    return count


def _bullets(lines: list[str]) -> str:
    return "\n".join(f"- {_trim(line)}" for line in lines)


@register_handler("secret-keeper")
class SecretKeeper(ManifestAgent):
    """Holds the dealt word and answers the guesser, never spelling it aloud, never asking.

    The word is stamped on every one of the keeper's events as a private ``secret``
    key — visible to the judge and the UI (which read payloads off the ledger) but never
    to the guesser (whose context is built from ``text`` only).  The keeper's spoken
    ``text`` is its yes/no answer; the secret stays out of it.

    Three disciplines keep the keeper an *answerer* that never leaks (the prior version
    both drifted into asking the guesser's questions back *and* spelled the word aloud):

      * the prompt names the guesser's most recent question and demands a truthful
        Yes/No answer about the held word;
      * a reply that asks a question or spells the word is re-asked once (with the reason
        fed back), then on a still-asking reply the turn is skipped; and
      * as an absolute guarantee, the spoken line is scrubbed of the word before it ships,
        so the secret can never reach the stage even if the model ignores every rule.
    """

    def _build_extra_prompt(self, projection: StageProjection, recent_events: tuple[Event, ...]) -> str:
        word = _word_for_seed(projection.seed)
        pairs = _qa_history(recent_events)
        # The guesser's most recent *open* question (the one awaiting this answer).
        question = next((q for q, a in reversed(pairs) if a is None), "")
        already = [a for _, a in pairs if a]
        guard = (
            "\n\nSTRICT RULES: You ANSWER, you never ask. Begin with 'Yes' or 'No' (then one short, "
            "truthful, playful clause). Answer ONLY about your secret word, and answer the SAME way "
            "every time about the same property — never contradict an earlier answer. Do NOT end your "
            f"line with a question mark. NEVER write the word '{word}' or any form of it — describe it, "
            "never name it; that instantly loses the game."
        )
        # On a corrective re-ask (set by ``act`` after a rejected reply), tell the model
        # exactly what it did wrong so the retry actually fixes it.
        reason = getattr(self, "_retry_reason", None)
        correction = f"\n\nCORRECTION: your previous reply was rejected because {reason}. Try again." if reason else ""
        if not question:
            return (
                f"YOUR SECRET WORD (never write, spell, or quote it — only answer about it): {word}\n"
                "The guesser has not asked yet. In ONE short sentence, invite them to begin asking "
                "yes/no questions. Do not reveal anything about the word yet." + guard + correction
            )
        consistency = (
            ("\n\nYour earlier answers (stay consistent with these):\n" + _bullets(already[-6:])) if already else ""
        )
        return (
            f"YOUR SECRET WORD (never write, spell, or quote it — only answer about it): {word}\n"
            f'The guesser just asked: "{_trim(question)}"\n'
            "Answer THAT question about your word, truthfully, in ONE short sentence."
            + consistency
            + guard
            + correction
        )

    def act(
        self,
        run_id: str,
        turn: int,
        projection: StageProjection,
        recent_events: tuple[Event, ...],
    ) -> Event:
        offline = bool(getattr(self.router, "offline", False))
        word = _word_for_seed(projection.seed)
        event = super().act(run_id, turn, projection, recent_events)
        usage_total = dict(self.last_usage)
        text = str(event.payload.get("text", ""))

        # Live quality pass: if the keeper asked a question or spelled the word, re-ask once
        # with the reason fed back. Offline lines are curated, leak-free answers, so this
        # never fires there — the deterministic path is identical. The retry's tokens are
        # summed so the governor still meters both calls (mirrors base ``_verify_verdict``).
        if not offline and _answer_violation(text, word):
            self._retry_reason = _answer_violation(text, word)
            try:
                retry = super().act(run_id, turn, projection, recent_events)
            finally:
                self._retry_reason = None
            usage_total = self._sum_usage(usage_total, dict(self.last_usage))
            self.last_usage = usage_total
            event, text = retry, str(retry.payload.get("text", ""))

        # Absolute guarantee: scrub any spelled-out word before it ships. A no-op offline
        # and on a clean reply; the safety net when the model leaks anyway (as it did with
        # "a glowing ember…"). Logged so a persistent leak is visible in the run.
        scrubbed = _redact_word(text, word)
        if scrubbed != text:
            obs.log("twenty_sprouts.redacted_keeper_leak", agent=self.name, turn=turn)
            text = scrubbed
            event.payload["text"] = text

        # The keeper must answer, not ask: a reply still ending in a question after the
        # re-ask is skipped (live only) rather than shown asking. Redaction above can't fix
        # an asked question, so this is the final gate.
        if not offline and _ENDS_QUESTION.search(text):
            raise AgentOutputError(f"{self.name}: keeper asked a question instead of answering — skipped")

        # Ground truth on the ledger, private (non-``text``) so it never reaches the
        # guesser's prompt — the judge reads it back at the reckoning, the UI shows it
        # to the human audience only.
        event.payload["secret"] = word
        return event


@register_handler("sprout-guesser")
class SproutGuesser(ManifestAgent):
    """Asks the questions — building on the ledger so it never repeats and always narrows.

    The base agent already sees the blackboard, but a small model left to itself loops on
    near-identical questions and forgets what it learned (the transcript that prompted this
    handler).  So we hand it an explicit dossier mined from the run's own Q&A:

      * CONFIRMED — questions the keeper answered *yes* (the shape of the word so far);
      * RULED OUT — questions answered *no* (dead ends to avoid);
      * ALREADY ASKED — every prior question, which it is told never to repeat or reword.

    With the facts in front of it the model is asked for ONE new splitting question — but
    it must also *commit guesses between questions* rather than interrogating forever (the
    failure the second transcript showed: twenty questions, never a guess). So the handler
    forces a committed ``My guess is: <word>`` when the facts converge, periodically once
    it has learned enough, and as a hard stop before the round runs out. A wrong guess just
    rolls into the ALREADY GUESSED list and play continues.
    """

    _COMMIT_AFTER = 12  # hard stop: this many questions in, stop asking and commit
    _GUESS_WHEN_CONFIRMED = 5  # this many YES facts ⇒ you almost certainly know it — guess now
    _MIN_FACTS_TO_GUESS = 3  # never force a guess before the word has real shape
    _GUESS_EVERY = 4  # once warmed up, take a guess at least every N questions

    def _build_extra_prompt(self, projection: StageProjection, recent_events: tuple[Event, ...]) -> str:
        pairs = _qa_history(recent_events)
        if not pairs:
            return (
                "You are opening a game of twenty questions. Ask ONE broad yes/no question that "
                "splits the space of possible woodland words roughly in half (e.g. alive vs. made, "
                "natural vs. crafted). One short sentence, ending in a question mark."
            )
        confirmed = [q for q, a in pairs if a and _classify_answer(a) == "yes"]
        ruled_out = [q for q, a in pairs if a and _classify_answer(a) == "no"]
        asked = [q for q, _ in pairs if not _FINAL_GUESS.search(q)]
        guessed = [q for q, _ in pairs if _FINAL_GUESS.search(q)]

        blocks: list[str] = ["YOUR DOSSIER on the keeper's secret word (read it before you ask):"]
        if confirmed:
            blocks.append("CONFIRMED — the keeper said YES to these (build on them):\n" + _bullets(confirmed))
        if ruled_out:
            blocks.append("RULED OUT — the keeper said NO to these (don't go back here):\n" + _bullets(ruled_out))
        if asked:
            blocks.append("ALREADY ASKED — never ask any of these again, or a reworded version:\n" + _bullets(asked))
        if guessed:
            blocks.append("ALREADY GUESSED (wrong) — never repeat these guesses:\n" + _bullets(guessed))

        if self._should_commit(asked, confirmed, pairs):
            differ = " Your earlier guess was wrong, so pick a DIFFERENT word." if guessed else ""
            task = (
                "TIME TO GUESS. Do NOT ask a question this turn. Read the CONFIRMED facts and name the "
                'single word they point to, written EXACTLY as: "My guess is: <word>."' + differ
            )
        else:
            task = (
                "Ask ONE genuinely NEW yes/no question that builds on the CONFIRMED facts and rules out "
                "more of the field. Go broad → specific. One short sentence ending in a question mark. "
                "Do NOT repeat or reword anything ALREADY ASKED."
            )
        return "\n\n".join(blocks) + "\n\n" + task

    def _should_commit(self, asked: list[str], confirmed: list[str], pairs: list[tuple[str, str | None]]) -> bool:
        """Decide whether this turn must be a guess rather than another question.

        Commit when any of: the round is nearly spent (``_COMMIT_AFTER`` questions), the word
        has plainly taken shape (``_GUESS_WHEN_CONFIRMED`` confirmed facts), or we have learned
        enough and have gone too long without guessing (``_GUESS_EVERY`` questions since the
        last guess) — the last is what *interleaves* guesses so the guesser stops interrogating
        forever."""
        if len(asked) >= self._COMMIT_AFTER:
            return True
        if len(confirmed) >= self._GUESS_WHEN_CONFIRMED:
            return True
        return len(confirmed) >= self._MIN_FACTS_TO_GUESS and _questions_since_guess(pairs) >= self._GUESS_EVERY


@register_handler("sprout-judge")
class SproutJudge(JudgedCompetition):
    """Decides Twenty Sprouts in code, and ends the game the moment the word is guessed.

    The guesser wins if the dealt word appears in **any** of its guesses — not only its
    last line. (The earlier version checked the most recent line, so a correct "compass"
    followed by more guessing was scored a miss — the bug this fixes.) Because the handler
    subscribes to ``agent.spoke``, :meth:`has_early_winner` lets it rule the instant a
    correct guess lands rather than waiting for the timeout tick; if the word is never
    named, it rules for the keeper at the finale. Winner is the **agent name**
    (``sprout-guesser`` / ``secret-keeper``) so winner→model attribution maps through the
    cast, and a ``reveal`` unmasks the word for the verdict banner.
    """

    def has_early_winner(self, recent_events: tuple[Event, ...]) -> bool:
        # End the show as soon as the guesser names the word — don't run on to the timeout.
        secret = self._dealt_word(recent_events)
        return bool(secret) and self._guessed(recent_events, secret)

    def decide_winner(
        self,
        event: Event,
        candidates: list[str],
        recent_events: tuple[Event, ...],
    ) -> str | None:
        secret = self._dealt_word(recent_events)
        if not secret:
            return super().decide_winner(event, candidates, recent_events)
        caught = self._guessed(recent_events, secret)
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
    def _guessed(recent_events: tuple[Event, ...], secret: str) -> bool:
        """True if the dealt word appears in ANY guesser line — a win sticks once made.

        Scanning every guesser ``agent.spoke`` (not just the latest) is the fix for the
        live bug where a correct guess was later buried under further guessing and the
        round was wrongly scored a miss."""
        needle = secret.lower()
        return any(
            e.actor == _GUESSER_NAME
            and e.kind == "agent.spoke"
            and needle in set(_WORD.findall(str(e.payload.get("text", "")).lower()))
            for e in recent_events
        )
