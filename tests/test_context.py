"""ContextBuilder — the shared blackboard reaches the prompt (ADR-0023).

Before this, an agent saw only the world text and its own past lines, so small
models looped on one clue and never reacted to anyone. The builder now surfaces
``projection.agent_notes`` (peers' public lines) so the table is actually shared.
"""

from __future__ import annotations

from src.core.context import ContextBuilder
from src.core.projections import StageProjection


def test_blackboard_surfaces_peer_lines():
    proj = StageProjection(goal="g", current_scene="scene")
    proj.agent_notes = ["spy-cara: a morning fuel", "spy-nil: warm and comforting"]
    prompt = ContextBuilder().build(agent_name="spy-bex", persona="(bex)", projection=proj, all_events=())
    assert "WHAT'S BEEN SAID" in prompt
    assert "a morning fuel" in prompt
    assert "warm and comforting" in prompt
    # nudges toward a fresh contribution, not an echo
    assert "new angle" in prompt.lower() and "echo" in prompt.lower()


def test_blackboard_prompts_the_first_speaker_to_open():
    proj = StageProjection(goal="g", current_scene="scene")  # no notes yet
    prompt = ContextBuilder().build(agent_name="spy-cara", persona="(cara)", projection=proj, all_events=())
    assert "WHAT'S BEEN SAID" in prompt
    assert "you go first" in prompt.lower()


def test_persona_and_goal_still_lead():
    proj = StageProjection(goal="catch the spy", current_scene="scene")
    prompt = ContextBuilder().build(agent_name="a", persona="I am A.", projection=proj, all_events=())
    # IDENTITY and SHARED GOAL must still come before the blackboard.
    assert prompt.index("I am A.") < prompt.index("catch the spy") < prompt.index("WHAT'S BEEN SAID")


def test_a_peer_thought_never_reaches_another_agent():
    # A spoken event carries a private `thought` (the mind-reader content). It must
    # ride only on its own payload — peers see the public `text`, never the thought.
    from src.core.events import Event

    proj = StageProjection(goal="g", current_scene="scene")
    proj.apply(
        Event(
            run_id="r",
            turn=1,
            kind="agent.spoke",
            actor="spy-nil",
            payload={"text": "warm and comforting", "thought": "I'm the spy — keep it vague!"},
        )
    )
    assert any("warm and comforting" in n for n in proj.agent_notes)
    assert not any("spy" in n.lower() and "vague" in n.lower() for n in proj.agent_notes)
    prompt = ContextBuilder().build(agent_name="spy-bex", persona="(bex)", projection=proj, all_events=())
    assert "warm and comforting" in prompt
    assert "keep it vague" not in prompt
