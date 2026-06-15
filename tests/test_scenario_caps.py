"""Mock-free tests for the Lab's scenario-capability lens (Unit 1).

``scenario_ui_caps`` is the pure, read-only derivation the Lab uses to decide which
controls are even meaningful for a run: it answers "does this (possibly edited) cast
have a judge?" and "which agents may use tools?".  These tests pin that behaviour
against the real registry — no judge for judge-less worlds, a judge otherwise, no
judge when the user drops it from the roster, and a tool agent only where one exists.
"""

from __future__ import annotations

import pytest

from src.core.registry import default_registry
from src.ui.fishbowl.scenario_caps import scenario_ui_caps

# Scenarios with / without a judge in their stock cast (per the capability matrix).
# Open Table gained a table-judge with the arena verdict (ADR-0029); Oracle Grove stays
# a judge-less tool-use showcase.
_JUDGED = ["thousand-token-wood", "mystery-roots", "the-steeped", "open-table"]
_JUDGELESS = ["oracle-grove"]


@pytest.mark.parametrize("name", _JUDGELESS)
def test_judgeless_scenarios_have_no_judge(name):
    caps = scenario_ui_caps(name)
    assert caps.judge is None
    assert caps.has_judge is False


@pytest.mark.parametrize("name", _JUDGED)
def test_judged_scenarios_expose_their_judge(name):
    caps = scenario_ui_caps(name)
    assert caps.judge is not None
    assert caps.judge.role == "judge"
    assert caps.has_judge is True
    # The judge is part of the effective cast but never a "worker" the §03 cards bind.
    assert caps.judge.name in caps.cast_names
    assert caps.judge.name not in {m.name for m in caps.worker_cast}


def test_dropping_the_judge_from_the_roster_hides_it():
    registry = default_registry()
    scenario = registry.scenarios["thousand-token-wood"]
    judge = next(n for n in scenario.cast if registry.agents[n].role == "judge")
    roster = [n for n in scenario.cast if n != judge]

    caps = scenario_ui_caps(scenario, cast_override=roster)
    assert caps.has_judge is False
    assert judge not in caps.cast_names


def test_oracle_grove_tool_agent_is_the_only_tool_capable_mind():
    caps = scenario_ui_caps("oracle-grove")
    # Exactly the fortune-teller may use a tool, and the tool is the oracle.
    assert caps.has_tools is True
    assert caps.tool_agents == {"fortune-teller": ["oracle"]}
    # The non-tool mind (scene-whisperer) is in the cast but not in tool_agents.
    assert "scene-whisperer" in caps.cast_names
    assert "scene-whisperer" not in caps.tool_agents


def test_open_table_conversational_seats_grant_no_tools():
    # The four conversational seats carry no tools; only the color commentator brings
    # its best-effort media tools (image.render / tts.speak), so the tool picker is
    # drawn for it alone.
    caps = scenario_ui_caps("open-table")
    assert caps.tool_agents == {"rafters-critic": ["image.render", "tts.speak"]}
    assert caps.has_tools is True


def test_available_agents_covers_the_whole_registry():
    caps = scenario_ui_caps("thousand-token-wood")
    registry = default_registry()
    assert {m.name for m in caps.available_agents} == set(registry.agents)


def test_roster_override_can_add_a_judge_to_a_judgeless_world():
    # Adding a judge agent to open-table's roster surfaces a Judge section.
    caps = scenario_ui_caps("open-table", cast_override=["chat-host", "mystery-judge"])
    assert caps.has_judge is True
    assert caps.judge.name == "mystery-judge"


def test_unknown_scenario_name_raises():
    with pytest.raises(ValueError, match="unknown scenario"):
        scenario_ui_caps("not-a-real-world")
