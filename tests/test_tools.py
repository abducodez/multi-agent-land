from __future__ import annotations

import pytest

from src.core.conductor import Conductor
from src.core.registry import default_registry
from src.tools.builtins import default_tool_registry, oracle
from src.tools.registry import CapabilityViolation, ToolRegistry


class TestOracleTool:
    def test_deterministic(self):
        assert oracle(seed="the glass forest") == oracle(seed="the glass forest")

    def test_returns_omen(self):
        assert "omen" in oracle(seed="anything")


class TestToolRegistry:
    def test_describe_lists_granted(self):
        tools = default_tool_registry()
        assert "oracle" in tools.describe(["oracle"])

    def test_describe_skips_unknown(self):
        assert default_tool_registry().describe(["nope"]) == ""

    def test_capability_enforced(self):
        reg = default_registry()
        tools = default_tool_registry()
        no_grant = reg.agents["scene-whisperer"]  # tools: []
        with pytest.raises(CapabilityViolation):
            tools.call("scene-whisperer", no_grant, "oracle", {"seed": "x"})

    def test_granted_but_unregistered_tool_raises_keyerror(self):
        reg = default_registry()
        empty = ToolRegistry()  # nothing registered
        granted = reg.agents["fortune-teller"]  # has 'oracle' grant
        with pytest.raises(KeyError):
            empty.call("fortune-teller", granted, "oracle", {})


class TestToolUsingAgentEndToEnd:
    def test_fortune_teller_calls_tool_and_records_omen(self):
        reg = default_registry()
        tools = default_tool_registry()
        scenario = reg.build_scenario("oracle-grove", tools=tools)
        conductor = Conductor(scenario, governor=reg.governor_for("oracle-grove"))
        conductor.reset(scenario.default_seed)
        for _ in range(2):
            conductor.step()

        oracle_events = [e for e in conductor.ledger.events if e.kind == "oracle.spoke"]
        assert oracle_events, "fortune-teller should emit its custom kind"
        assert "omen" in oracle_events[0].payload  # tool output is on the ledger

    def test_oracle_grove_loads_with_tool_handler(self):
        reg = default_registry()
        assert "oracle-grove" in reg.scenarios
        assert "fortune-teller" in reg.agents
        assert reg.agents["fortune-teller"].handler == "fortune-teller"
