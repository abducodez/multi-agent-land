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


def _spoke(actor, text, turn=1, kind="agent.spoke"):
    from src.core.events import Event

    return Event(run_id="r", turn=turn, kind=kind, actor=actor, payload={"text": text})


def test_judge_gets_the_full_ordered_transcript_not_just_the_tail():
    # A judge rules on the WHOLE discussion — every spoken line, in order — not the
    # recency-biased blackboard tail a worker reacts to (ADR-0023 follow-up).
    events = tuple(_spoke("debater-a" if i % 2 == 0 else "debater-b", f"point number {i}", turn=i) for i in range(12))
    proj = StageProjection(goal="g", current_scene="scene")
    prompt = ContextBuilder().build(
        agent_name="debate-judge", persona="(judge)", projection=proj, all_events=events, role="judge"
    )
    assert "THE EXCHANGE TO JUDGE" in prompt and "WHAT'S BEEN SAID" not in prompt
    # every line is present, oldest first
    assert "point number 0" in prompt and "point number 11" in prompt
    assert prompt.index("point number 0") < prompt.index("point number 11")


def test_judge_transcript_excludes_private_thoughts():
    # Thoughts are the mind-reader's alone; a judge rules on what was SAID.
    events = (_spoke("a", "said aloud"), _spoke("b", "secret scheming", kind="agent.thought"))
    prompt = ContextBuilder().build(
        agent_name="j",
        persona="(judge)",
        projection=StageProjection(current_scene="s"),
        all_events=events,
        role="judge",
    )
    assert "said aloud" in prompt
    assert "secret scheming" not in prompt


def test_worker_gets_blackboard_not_transcript():
    proj = StageProjection(current_scene="scene")
    proj.agent_notes = ["a: hello there"]
    prompt = ContextBuilder().build(
        agent_name="w", persona="(w)", projection=proj, all_events=(_spoke("a", "hello there"),), role="worker"
    )
    assert "WHAT'S BEEN SAID" in prompt and "THE EXCHANGE TO JUDGE" not in prompt


def test_memory_does_not_repeat_a_line_already_in_the_discussion():
    # A spoken line shown in the blackboard must not be printed again in YOUR MEMORY:
    # the union is unchanged, we just don't duplicate (blackboard=recent, memory=earlier).
    proj = StageProjection(current_scene="scene")
    proj.agent_notes = ["a: the bench needs shade"]
    memory_text = "[turn 003][agent.spoke] the bench needs shade\n[turn 001][world.observed] an older beat"
    prompt = ContextBuilder().build(
        agent_name="w", persona="(w)", projection=proj, all_events=(), memory_text=memory_text, role="worker"
    )
    # the duplicated line appears once (in the blackboard), the unique earlier beat survives in memory
    assert prompt.count("the bench needs shade") == 1
    assert "an older beat" in prompt


def test_memory_shows_a_pointer_when_fully_covered_by_the_discussion():
    proj = StageProjection(current_scene="scene")
    proj.agent_notes = ["a: only line"]
    prompt = ContextBuilder().build(
        agent_name="w",
        persona="(w)",
        projection=proj,
        all_events=(),
        memory_text="[turn 003][agent.spoke] only line",
        role="worker",
    )
    assert "nothing beyond the exchange above" in prompt


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
