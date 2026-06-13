"""Twenty Sprouts gameplay — the keeper answers, the guesser builds on the ledger.

These exercise the handlers' *pure* logic (Q&A reconstruction, dossier prompts, the
keeper's answer guard) plus the end-to-end discipline that matters most for a
hidden-word game: the secret word is dealt by code, shown to the human audience, and
never placed in any agent's prompt.  All offline — zero tokens, zero mocks.
"""

from __future__ import annotations

import pytest

from src.agents.twenty_sprouts import (
    _WORDS,
    SecretKeeper,
    SproutGuesser,
    _answer_violation,
    _classify_answer,
    _contains_word,
    _qa_history,
    _questions_since_guess,
    _redact_word,
    _word_for_seed,
)
from src.core.context import ContextBuilder
from src.core.conductor import Conductor
from src.core.events import Event
from src.core.governor import BudgetExceeded
from src.core.memory import EpisodicMemory
from src.core.projections import StageProjection, rebuild_stage
from src.core.registry import Registry
from src.models.router import ModelRouter
from src.tools.builtins import default_tool_registry
from src.ui.fishbowl.render.stage import render_constellation
from src.ui.fishbowl.view_model import view_model_at

_RUN = "r1"


def _spoke(actor: str, text: str, turn: int = 1) -> Event:
    return Event(run_id=_RUN, turn=turn, kind="agent.spoke", actor=actor, payload={"text": text})


def _qa(*pairs: tuple[str, str | None]) -> tuple[Event, ...]:
    """Build a guesser/keeper transcript from (question, answer|None) pairs."""
    events: list[Event] = []
    for i, (q, a) in enumerate(pairs, start=1):
        events.append(_spoke("sprout-guesser", q, turn=i))
        if a is not None:
            events.append(_spoke("secret-keeper", a, turn=i))
    return tuple(events)


# ── ground truth: the dealt word ────────────────────────────────────────────────


def test_word_is_a_deterministic_function_of_the_seed():
    seed = "A small thing from the wood"
    assert _word_for_seed(seed) == _word_for_seed(seed)
    assert _word_for_seed(seed) in _WORDS


def test_empty_seed_still_deals_a_valid_word():
    assert _word_for_seed("") in _WORDS


# ── Q&A reconstruction off the ledger ────────────────────────────────────────────


def test_qa_history_pairs_each_question_with_the_following_answer():
    events = _qa(("alive?", "No, never alive."), ("made by hands?", "Yes, crafted."))
    assert _qa_history(events) == [("alive?", "No, never alive."), ("made by hands?", "Yes, crafted.")]


def test_qa_history_leaves_an_unanswered_trailing_question_open():
    events = _qa(("alive?", "No."), ("does it glow?", None))
    pairs = _qa_history(events)
    assert pairs[-1] == ("does it glow?", None)


def test_qa_history_ignores_a_keeper_line_with_no_open_question():
    # A stray keeper line before any question must not attach to nothing or crash.
    events = (_spoke("secret-keeper", "Ask me anything."), _spoke("sprout-guesser", "alive?"))
    assert _qa_history(events) == [("alive?", None)]


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("Yes, you could hold it.", "yes"),
        ("No, never alive.", "no"),
        ("Never — not in a hundred winters.", "no"),
        ("Mostly, on cold nights.", "yes"),
        ("Warmer now — it belongs to the wood.", "hint"),
    ],
)
def test_classify_answer_reads_the_lead_word(answer: str, expected: str):
    assert _classify_answer(answer) == expected


# ── the keeper: answers, never asks ──────────────────────────────────────────────


def _keeper() -> SecretKeeper:
    agent = SecretKeeper(ModelRouter(offline=True, specs={}))
    agent.manifest = Registry.from_dir().agents["secret-keeper"]
    return agent


def _guesser() -> SproutGuesser:
    agent = SproutGuesser(ModelRouter(offline=True, specs={}))
    agent.manifest = Registry.from_dir().agents["sprout-guesser"]
    return agent


def test_keeper_prompt_carries_the_word_and_the_open_question():
    proj = StageProjection(seed="seed-A")
    events = _qa(("Is it alive?", None))
    prompt = _keeper()._build_extra_prompt(proj, events)
    assert _word_for_seed("seed-A") in prompt  # the keeper knows its word
    assert "Is it alive?" in prompt  # and the exact question to answer
    assert "never ask" in prompt.lower()  # and the discipline to only answer


def test_keeper_first_turn_invites_the_guesser_to_begin():
    prompt = _keeper()._build_extra_prompt(StageProjection(seed="seed-A"), ())
    assert "not asked yet" in prompt.lower() or "invite" in prompt.lower()


def test_keeper_prompt_forbids_writing_the_actual_word():
    word = _word_for_seed("seed-A")
    prompt = _keeper()._build_extra_prompt(StageProjection(seed="seed-A"), _qa(("Is it alive?", None)))
    assert f"NEVER write the word '{word}'" in prompt  # the word it must never spell


def test_keeper_question_guard_is_a_passthrough_offline():
    # Offline the curated keeper lines are answers, so a full offline turn never trips
    # the live-only question/leak guard — proving the deterministic path is unchanged.
    keeper = _keeper()
    event = keeper.act(_RUN, 1, StageProjection(seed="seed-A"), _qa(("Is it alive?", None)))
    assert event.kind == "agent.spoke"
    assert event.payload["secret"] == _word_for_seed("seed-A")  # ground truth stamped on


# ── the keeper never leaks the word (the worst bug: it spelled "ember" aloud) ─────


@pytest.mark.parametrize(
    "text,word,leaks",
    [
        ("Yes, a glowing ember can emerge from the fire.", "EMBER", True),
        ("Yes, embers like it glow.", "EMBER", True),  # simple inflection
        ("No, it never drives anywhere.", "RIVER", False),  # substring, not the word
        ("Yes, you could hold it in one hand.", "EMBER", False),
    ],
)
def test_contains_word_is_whole_word_and_inflection_aware(text: str, word: str, leaks: bool):
    assert _contains_word(text, word) is leaks


def test_redact_word_scrubs_the_secret_before_it_ships():
    scrubbed = _redact_word("Yes, a glowing ember can emerge from the fire.", "EMBER")
    assert "ember" not in scrubbed.lower()
    assert scrubbed.startswith("Yes,")  # the rest of the answer survives


def test_redact_word_is_a_noop_when_the_word_is_absent():
    line = "No, it was never alive, though plenty have leaned on it."
    assert _redact_word(line, "EMBER") == line


@pytest.mark.parametrize(
    "text,word,reason_substr",
    [
        ("Yes, an ember glows.", "EMBER", "spelled"),  # leak
        ("Is it warm to hold?", "EMBER", "asked a question"),  # asking, not answering
        ("Yes, you could hold it patiently.", "EMBER", None),  # clean answer
    ],
)
def test_answer_violation_flags_leaks_and_questions(text: str, word: str, reason_substr: str | None):
    reason = _answer_violation(text, word)
    if reason_substr is None:
        assert reason is None
    else:
        assert reason and reason_substr in reason


# ── the guesser: builds on the ledger, never repeats ─────────────────────────────


def test_guesser_dossier_sorts_confirmed_and_ruled_out():
    events = _qa(
        ("Is it made by hands?", "Yes, crafted by someone patient."),
        ("Is it alive?", "No, never alive."),
    )
    prompt = _guesser()._build_extra_prompt(StageProjection(seed="seed-A"), events)
    assert "CONFIRMED" in prompt and "Is it made by hands?" in prompt
    assert "RULED OUT" in prompt and "Is it alive?" in prompt
    assert "ALREADY ASKED" in prompt  # every prior question, never to be repeated


def test_guesser_opening_prompt_when_nothing_asked_yet():
    prompt = _guesser()._build_extra_prompt(StageProjection(seed="seed-A"), ())
    assert "opening" in prompt.lower() or "broad" in prompt.lower()


def test_guesser_hard_commits_after_the_question_cap():
    # All "No" answers (no confirmed facts), so only the hard _COMMIT_AFTER stop can fire.
    asked = tuple((f"Is it property number {i}?", "No, not that.") for i in range(SproutGuesser._COMMIT_AFTER + 1))
    prompt = _guesser()._build_extra_prompt(StageProjection(seed="seed-A"), _qa(*asked))
    assert "time to guess" in prompt.lower() and "my guess is" in prompt.lower()


def test_guesser_commits_once_the_word_has_clearly_taken_shape():
    # _GUESS_WHEN_CONFIRMED yes-answers ⇒ commit even though few questions were asked.
    yes = tuple((f"Is it trait {i}?", "Yes, that's right.") for i in range(SproutGuesser._GUESS_WHEN_CONFIRMED))
    prompt = _guesser()._build_extra_prompt(StageProjection(seed="seed-A"), _qa(*yes))
    assert "time to guess" in prompt.lower()


def test_guesser_interleaves_a_guess_after_too_many_questions_without_one():
    # Enough facts to guess, and _GUESS_EVERY questions since the last guess ⇒ forced guess,
    # so the guesser commits *between* questions instead of interrogating forever.
    pairs = [("Is it made by hands?", "Yes."), ("Is it warm?", "Yes."), ("Is it small?", "Yes.")]
    pairs += [(f"Is it trait {i}?", "No.") for i in range(SproutGuesser._GUESS_EVERY)]
    prompt = _guesser()._build_extra_prompt(StageProjection(seed="seed-A"), _qa(*pairs))
    assert "time to guess" in prompt.lower()


def test_guesser_keeps_asking_when_it_has_too_little_and_guessed_recently():
    # One confirmed fact, just asked a couple questions: not enough to force a guess yet.
    prompt = _guesser()._build_extra_prompt(
        StageProjection(seed="seed-A"), _qa(("Is it warm?", "Yes."), ("Is it loud?", "No."))
    )
    assert "time to guess" not in prompt.lower()
    assert "yes/no question" in prompt.lower()


def test_questions_since_guess_counts_back_to_the_last_commit():
    pairs = _qa_history(_qa(("q1?", "No."), ("My guess is: ACORN", "No."), ("q2?", "No."), ("q3?", "No.")))
    assert _questions_since_guess(pairs) == 2  # two questions after the guess
    assert _questions_since_guess(_qa_history(_qa(("q1?", "No."), ("q2?", "No.")))) == 2  # never guessed


def test_guesser_lists_a_prior_wrong_guess_so_it_is_not_repeated():
    events = _qa(("Is it alive?", "No."), ("My guess is: ACORN", "No, that's not it."))
    prompt = _guesser()._build_extra_prompt(StageProjection(seed="seed-A"), events)
    assert "ALREADY GUESSED" in prompt and "ACORN" in prompt


# ── the whole game: secret to the audience, never to the cast ────────────────────


@pytest.fixture(scope="module")
def played():
    """Run Twenty Sprouts to a verdict on the deterministic stub; return (events, word)."""
    registry = Registry.from_dir()
    scenario = registry.build_scenario(
        "twenty-sprouts", router=ModelRouter(offline=True, specs={}), tools=default_tool_registry()
    )
    conductor = Conductor(scenario, governor=registry.governor_for("twenty-sprouts"))
    conductor.reset(scenario.default_seed)
    for _ in range(80):
        try:
            conductor.step()
        except BudgetExceeded:
            break
        if any(e.kind == "judge.verdict" for e in conductor.ledger.events_for_run(conductor.run_id)):
            break
    events = conductor.ledger.events_for_run(conductor.run_id)
    return events, _word_for_seed(scenario.default_seed), [a.manifest for a in scenario.agents]


def test_secret_never_enters_the_guessers_context(played):
    events, word, _cast = played
    proj = rebuild_stage(events)
    memory = EpisodicMemory("sprout-guesser", max_recent=40).format_for_prompt(events)
    context = ContextBuilder().build(
        agent_name="sprout-guesser",
        persona="P",
        projection=proj,
        all_events=events,
        memory_window=40,
        memory_text=memory,
    )
    assert word not in context
    assert word not in memory
    assert word not in "\n".join(proj.agent_notes)


def test_secret_reaches_the_audience_view_model_and_stage(played):
    events, word, cast = played
    vm = view_model_at(events, len(events), cast, scenario_name="twenty-sprouts")
    assert vm["secret"] == word
    assert vm["secret_holder"] == "secret-keeper"
    stage_html = render_constellation(vm, {})
    assert word in stage_html and "only you can see" in stage_html


def test_secret_does_not_leak_into_the_narrator_feed(played):
    events, word, cast = played
    vm = view_model_at(events, len(events), cast, scenario_name="twenty-sprouts")
    feed_text = " ".join(str(item.get("said") or item.get("text") or "") for item in vm["feed"])
    assert word not in feed_text


def test_non_secret_scenarios_have_no_audience_badge():
    # A run with no dealt secret leaves the field None and renders no badge.
    cast = list(Registry.from_dir().agents.values())[:1]
    vm = view_model_at((), 0, cast, scenario_name="debate-duel")
    assert vm["secret"] is None and vm["secret_holder"] is None
    assert "only you can see" not in render_constellation(vm, {})
