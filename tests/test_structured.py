from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.structured import (
    AgentOutputError,
    build_output_model,
    clean_clue,
    extract_reasoning,
    is_usable_line,
    json_instruction,
    parse_agent_output,
)


class TestJsonInstruction:
    def test_returns_string(self):
        out = json_instruction(["agent.spoke", "agent.thought"])
        assert isinstance(out, str)
        assert "agent.spoke" in out
        assert "agent.thought" in out

    def test_contains_schema_hint(self):
        out = json_instruction(["agent.spoke"])
        assert "kind" in out
        assert "text" in out

    def test_extra_fields_appear(self):
        out = json_instruction(["agent.spoke"], extra_fields=["emotion"])
        assert "emotion" in out


class TestParseAgentOutput:
    def test_valid_json_parsed(self):
        raw = '{"kind": "agent.spoke", "text": "I collect echoes."}'
        result = parse_agent_output(raw, ["agent.spoke", "agent.thought"], "agent.spoke")
        assert result["kind"] == "agent.spoke"
        assert result["text"] == "I collect echoes."

    def test_invalid_kind_replaced_by_fallback(self):
        raw = '{"kind": "not.real", "text": "oops"}'
        result = parse_agent_output(raw, ["agent.spoke"], "agent.spoke")
        assert result["kind"] == "agent.spoke"

    def test_missing_kind_uses_fallback(self):
        raw = '{"text": "just text"}'
        result = parse_agent_output(raw, ["agent.spoke"], "agent.spoke")
        assert result["kind"] == "agent.spoke"

    def test_json_embedded_in_prose(self):
        raw = 'Here is my response: {"kind": "agent.spoke", "text": "The moon filed a complaint."}'
        result = parse_agent_output(raw, ["agent.spoke"], "agent.spoke")
        assert result["kind"] == "agent.spoke"
        assert "moon" in result["text"]

    def test_pure_text_fallback(self):
        raw = "The mushrooms charge admission."
        result = parse_agent_output(raw, ["agent.spoke"], "agent.spoke")
        assert result["kind"] == "agent.spoke"
        assert "mushrooms" in result["text"]
        assert result.get("_raw_fallback") is True

    def test_empty_string_fallback(self):
        result = parse_agent_output("", ["agent.spoke"], "agent.spoke")
        assert result["kind"] == "agent.spoke"

    def test_extra_fields_preserved(self):
        raw = '{"kind": "agent.spoke", "text": "hello", "emotion": "puzzled"}'
        result = parse_agent_output(raw, ["agent.spoke"], "agent.spoke")
        assert result.get("emotion") == "puzzled"

    def test_first_allowed_kind_is_fallback(self):
        raw = "plain prose"
        result = parse_agent_output(raw, ["judge.verdict", "agent.spoke"], "judge.verdict")
        assert result["kind"] == "judge.verdict"


class TestParserHardening:
    """The leak we saw live: a reasoning model dumps its chain-of-thought (which
    names the secret word) and the old parser shipped it as the spoken line."""

    def test_strips_think_block_and_parses_json(self):
        raw = '<think>the word is COFFEE, stay vague</think>\n{"kind":"agent.spoke","text":"Fuel for the morning."}'
        r = parse_agent_output(raw, ["agent.spoke"], "agent.spoke")
        assert r["text"] == "Fuel for the morning."
        assert "COFFEE" not in str(r).upper()

    def test_strips_code_fences(self):
        raw = '```json\n{"kind":"agent.spoke","text":"fenced line"}\n```'
        r = parse_agent_output(raw, ["agent.spoke"], "agent.spoke")
        assert r["text"] == "fenced line"

    def test_prefers_last_balanced_object(self):
        # A model that plans in a draft object then emits the real one puts it last.
        raw = 'draft {"kind":"agent.spoke","text":"junk"} final {"kind":"agent.spoke","text":"the real line"}'
        r = parse_agent_output(raw, ["agent.spoke"], "agent.spoke")
        assert r["text"] == "the real line"

    def test_handles_braces_inside_string_values(self):
        # The old flat \\{[^{}]+\\} regex truncated on inner braces; the scan is string-aware.
        raw = '{"kind":"agent.spoke","text":"a set {of} braces inside"}'
        r = parse_agent_output(raw, ["agent.spoke"], "agent.spoke")
        assert r["text"] == "a set {of} braces inside"

    def test_salvages_quoted_text_from_scratchpad(self):
        raw = 'We need JSON. Thought: I think the word is COFFEE. Text: "A dark, steaming brew." Done.'
        r = parse_agent_output(raw, ["agent.spoke"], "agent.spoke")
        assert r["text"] == "A dark, steaming brew."
        assert "COFFEE" not in r["text"].upper()
        assert r.get("_raw_fallback") is True

    def test_pure_scratchpad_degrades_to_safe_placeholder(self):
        raw = "We need to output JSON. Thought: I think the word is COFFEE. We"
        r = parse_agent_output(raw, ["agent.spoke"], "agent.spoke")
        assert "COFFEE" not in r["text"].upper()
        assert "JSON" not in r["text"].upper()

    def test_salvages_trailing_unterminated_quote(self):
        # Verbatim from the live ledger: a reasoning model started drafting the clue.
        raw = 'But must be one or two sentences. "A dark brew steams from a chipped mug'
        r = parse_agent_output(raw, ["agent.spoke"], "agent.spoke")
        assert r["text"] == "A dark brew steams from a chipped mug"

    def test_drops_meta_commentary_sentences(self):
        raw = "I should keep it vague. A dark brew warms the morning."
        r = parse_agent_output(raw, ["agent.spoke"], "agent.spoke")
        assert r["text"] == "A dark brew warms the morning."


class TestExtractReasoning:
    def test_pulls_inline_think_block(self):
        assert extract_reasoning("<think>plan: be vague</think> the clue") == "plan: be vague"

    def test_empty_when_no_tags(self):
        assert extract_reasoning("just an answer, no tags") == ""

    def test_trims_to_limit(self):
        long = "x" * 2000
        assert len(extract_reasoning(f"<think>{long}</think>", limit=100)) == 100


class TestCleanClue:
    """The live prose fallback: extract a clean spoken line, never the scratchpad."""

    def test_keeps_a_plain_clue(self):
        clue, residue = clean_clue("A dark, steaming cup that whispers of bitterness and depth.")
        assert clue == "A dark, steaming cup that whispers of bitterness and depth."
        assert residue == ""

    def test_drops_secret_word_sentence(self):
        # Verbatim shape of the live leak: the model named COFFEE while reasoning.
        clue, residue = clean_clue(
            'Also include "thought" and "mood". Secret word is COFFEE. A dark brew warms the dawn.'
        )
        assert "COFFEE" not in clue.upper()
        assert clue == "A dark brew warms the dawn."

    def test_drops_instruction_echo_and_example(self):
        assert clean_clue("Need to output JSON with kind agent.spoke, text one or two sentences.")[0] == ""
        assert clean_clue("A brief, evocative response.")[0] == ""

    def test_residue_carries_the_thinking(self):
        clue, residue = clean_clue("<think>I'm the spy, stay calm</think> A warm cup soothes the morning.")
        assert clue == "A warm cup soothes the morning."
        assert "spy" in residue

    def test_unterminated_think_yields_no_clue_but_keeps_thought(self):
        # Verbatim live shape: truncated mid-think, no closing tag, and it names the word.
        raw = "<think>Alright, the user wants me to play as CARA. Since COFFEE is common, I shou"
        clue, residue = clean_clue(raw)
        assert clue == ""  # all reasoning, no answer → caller skips the turn
        assert "COFFEE" not in clue.upper()
        assert "COFFEE" in residue.upper()  # preserved as the (private) mind-reader thought

    def test_all_caps_token_sentence_is_dropped(self):
        # A generic secret-word guard: a sentence with an ALL-CAPS token is never the clue.
        clue, _ = clean_clue("It pairs with TEA at dawn. A pale gold warmth fills the cup.")
        assert "TEA" not in clue.upper()
        assert clue == "A pale gold warmth fills the cup."

    def test_first_person_opener_survives_when_it_is_all_there_is(self):
        # The spy-bex failure: an over-thinker's whole line is a first-person opener. Soft
        # meta alone must be PROMOTED to the clue, not stripped to nothing (no usable line).
        clue, _ = clean_clue("I think mine is something rooted, tall, and still.")
        assert clue == "I think mine is something rooted, tall, and still."
        assert is_usable_line(clue)

    def test_first_person_opener_yields_to_real_speech(self):
        # When a plain clue survives alongside the first-person preamble, the preamble drops
        # to residue and the clean spoken line wins.
        clue, residue = clean_clue("I think mine is the odd one. A pale gold warmth fills the cup.")
        assert clue == "A pale gold warmth fills the cup."
        assert "I think" in residue


class TestIsUsableLine:
    def test_rejects_empty_placeholder_and_example(self):
        assert not is_usable_line("")
        assert not is_usable_line("…")
        assert not is_usable_line("  …  ")
        assert not is_usable_line("A brief, evocative response.")

    def test_accepts_a_real_line(self):
        assert is_usable_line("A dark brew warms the dawn.")


class TestBuildOutputModel:
    """The live-path schema. kind is Literal-constrained, text is required, and the
    well-known verdict fields (ADR-0029) get real optional types — everything else
    stays a required string, exactly as before."""

    def test_requires_at_least_one_kind(self):
        with pytest.raises(AgentOutputError):
            build_output_model([])

    def test_kind_is_literal_constrained(self):
        model = build_output_model(["judge.verdict"])
        with pytest.raises(ValidationError):
            model(kind="not.allowed", text="x")

    def test_text_is_required(self):
        model = build_output_model(["agent.spoke"])
        with pytest.raises(ValidationError):
            model(kind="agent.spoke")

    def test_ordinary_extra_field_is_required_string(self):
        # The *other* row: an arbitrary scenario field stays a required str (back-compat).
        model = build_output_model(["agent.spoke"], extra_fields=["mood"])
        with pytest.raises(ValidationError):
            model(kind="agent.spoke", text="hi")  # mood missing → invalid
        inst = model(kind="agent.spoke", text="hi", mood="calm")
        assert inst.mood == "calm"

    def test_winner_is_optional_and_defaults_to_none(self):
        # A judge may decline to name a winner; the field must default to None, not error.
        model = build_output_model(["judge.verdict"], extra_fields=["winner"])
        inst = model(kind="judge.verdict", text="Verdict: undecided.")
        assert inst.winner is None

    def test_winner_accepts_a_name(self):
        model = build_output_model(["judge.verdict"], extra_fields=["winner"])
        inst = model(kind="judge.verdict", text="t", winner="clue-gatherer")
        assert inst.winner == "clue-gatherer"

    def test_scores_defaults_to_empty_map(self):
        model = build_output_model(["judge.verdict"], extra_fields=["scores"])
        inst = model(kind="judge.verdict", text="t")
        assert inst.scores == {}

    def test_scores_coerces_numeric_map(self):
        model = build_output_model(["judge.verdict"], extra_fields=["scores"])
        inst = model(kind="judge.verdict", text="t", scores={"clue-gatherer": 9})
        assert inst.scores == {"clue-gatherer": 9.0}

    def test_mixed_known_and_unknown_fields(self):
        # mood required, winner/scores optional — the full mystery-judge shape.
        model = build_output_model(["judge.verdict"], extra_fields=["mood", "winner", "scores"])
        inst = model(kind="judge.verdict", text="t", mood="smug")
        assert (inst.mood, inst.winner, inst.scores) == ("smug", None, {})


class TestJsonInstructionWellKnown:
    """The prompt hint. With NO well-known field present it must be byte-identical to
    the original uniform schema; with winner/scores present it renders typed hints."""

    @pytest.mark.parametrize("extra", [None, ["mood"], ["thought"], ["mood", "thought"]])
    def test_byte_identical_without_well_known_fields(self, extra):
        # The common case must not drift: the same schema-line rendering as before.
        out = json_instruction(["agent.spoke"], extra_fields=extra)
        fields = '", "'.join(["kind", "text", *(extra or [])])
        assert f'Schema: {{"{fields}": "..."}}' in out

    def test_winner_hint_appears_when_present(self):
        out = json_instruction(["judge.verdict"], extra_fields=["winner"])
        assert "winner" in out
        assert "or null" in out  # the typed hint, not the uniform "..."

    def test_scores_hint_appears_when_present(self):
        out = json_instruction(["judge.verdict"], extra_fields=["scores"])
        assert "0-10" in out  # a number-map hint, not a quoted string

    def test_ordinary_field_keeps_uniform_hint_alongside_known(self):
        # mood sits next to winner: it still gets "..." while winner gets its typed hint.
        out = json_instruction(["judge.verdict"], extra_fields=["mood", "winner", "scores"])
        assert '"mood": "..."' in out
        assert "or null" in out and "0-10" in out
