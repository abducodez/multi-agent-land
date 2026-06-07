from __future__ import annotations

from src.core.structured import json_instruction, parse_agent_output


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
