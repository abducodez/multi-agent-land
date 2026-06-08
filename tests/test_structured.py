from __future__ import annotations

from src.core.structured import (
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


class TestIsUsableLine:
    def test_rejects_empty_placeholder_and_example(self):
        assert not is_usable_line("")
        assert not is_usable_line("…")
        assert not is_usable_line("  …  ")
        assert not is_usable_line("A brief, evocative response.")

    def test_accepts_a_real_line(self):
        assert is_usable_line("A dark brew warms the dawn.")
