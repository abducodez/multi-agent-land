from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.core.config import (
    CompetitionConfig,
    ModelProfileConfig,
    ModelsConfig,
    ScenarioConfig,
    validate_agent,
    validate_scenario,
    validate_world,
)


class TestModelProfileConfig:
    def test_model_field_works(self):
        cfg = ModelProfileConfig(model="qwen2.5-3b-instruct")
        assert cfg.model == "qwen2.5-3b-instruct"
        assert cfg.temperature == 0.8

    def test_extra_rejected(self):
        with pytest.raises(ValidationError):
            ModelProfileConfig(model="m", bogus=1)  # type: ignore[call-arg]


class TestValidateAgent:
    def test_valid(self):
        m = validate_agent({"name": "seeker", "persona": "You seek.", "may_emit": ["world.observed"]})
        assert m.name == "seeker"

    def test_invalid_raises(self):
        with pytest.raises(ValidationError):
            validate_agent({"persona": "no name"})


class TestValidateScenario:
    def test_valid_with_goal_and_cast(self):
        s = validate_scenario({"name": "w", "goal": "be strange", "default_seed": "seed", "cast": ["a", "b"]})
        assert s.goal == "be strange"
        assert s.cast == ["a", "b"]


class TestValidateWorld:
    def test_coherent_world(self):
        world = validate_world(
            {
                "models": {"offline": True},
                "agents": [{"name": "a", "persona": "p", "may_emit": ["world.observed"]}],
                "scenarios": [{"name": "s", "default_seed": "seed", "cast": ["a"]}],
            }
        )
        assert isinstance(world.models, ModelsConfig)
        assert isinstance(world.scenarios[0], ScenarioConfig)

    def test_cast_referencing_undefined_agent_rejected(self):
        # The cross-check that makes UI/LLM-generated config safe to run.
        with pytest.raises(ValidationError) as exc:
            validate_world(
                {
                    "agents": [{"name": "a", "persona": "p"}],
                    "scenarios": [{"name": "s", "default_seed": "seed", "cast": ["ghost"]}],
                }
            )
        assert "undefined agents" in str(exc.value)


# ── competition contract (ADR-0029) ──────────────────────────────────────────────


class TestCompetitionConfig:
    """A scenario's winner contract — versus/judged/none and the team-shape rules
    a competition can enforce on its own (cross-cast checks live on WorldConfig)."""

    def test_default_is_none_with_no_teams(self):
        # An absent block == none; the field must default safely, never to versus.
        c = CompetitionConfig()
        assert c.kind == "none"
        assert c.teams is None

    def test_judged_needs_no_teams(self):
        c = CompetitionConfig(kind="judged")
        assert c.kind == "judged"
        assert c.teams is None

    @pytest.mark.parametrize("kind", ["none", "judged"])
    def test_teams_forbidden_unless_versus(self, kind):
        # teams on a non-versus kind is a config mistake — the winner has no team map.
        with pytest.raises(ValidationError) as exc:
            CompetitionConfig(kind=kind, teams={"spy": ["a"]})
        assert "only allowed when kind is 'versus'" in str(exc.value)

    def test_versus_requires_non_empty_teams(self):
        with pytest.raises(ValidationError) as exc:
            CompetitionConfig(kind="versus", teams={})
        assert "non-empty 'teams'" in str(exc.value)

    def test_versus_with_missing_teams_rejected(self):
        # kind=versus with no teams at all is the same defect as an empty mapping.
        with pytest.raises(ValidationError):
            CompetitionConfig(kind="versus")

    def test_versus_rejects_empty_member_list(self):
        # A team with no members can never win or lose — reject it at config time.
        with pytest.raises(ValidationError) as exc:
            CompetitionConfig(kind="versus", teams={"spy": ["spy-nil"], "herd": []})
        assert "empty member lists" in str(exc.value)
        assert "herd" in str(exc.value)

    def test_versus_rejects_overlapping_teams(self):
        # An agent on two teams makes "who won" ambiguous — disjointness is required.
        with pytest.raises(ValidationError) as exc:
            CompetitionConfig(kind="versus", teams={"spy": ["nil"], "herd": ["cara", "nil"]})
        assert "mutually disjoint" in str(exc.value)
        assert "nil" in str(exc.value)

    def test_versus_disjoint_teams_accepted(self):
        c = CompetitionConfig(kind="versus", teams={"spy": ["nil"], "herd": ["cara", "bex"]})
        assert c.teams == {"spy": ["nil"], "herd": ["cara", "bex"]}

    def test_same_member_repeated_within_one_team_is_not_overlap(self):
        # Overlap means across DIFFERENT labels; a dup inside one team is harmless here.
        c = CompetitionConfig(kind="versus", teams={"spy": ["nil", "nil"]})
        assert c.kind == "versus"

    def test_extra_field_forbidden(self):
        with pytest.raises(ValidationError):
            CompetitionConfig(kind="none", bogus=1)  # type: ignore[call-arg]


class TestScenarioCompetition:
    def test_scenario_accepts_competition_block(self):
        s = validate_scenario(
            {
                "name": "duel",
                "default_seed": "seed",
                "cast": ["a", "b"],
                "competition": {"kind": "versus", "teams": {"x": ["a"], "y": ["b"]}},
            }
        )
        assert s.competition is not None
        assert s.competition.kind == "versus"

    def test_scenario_without_competition_defaults_to_none_attribute(self):
        # Absent block == no competition object (the hook reads this as "none").
        s = validate_scenario({"name": "s", "default_seed": "seed", "cast": ["a"]})
        assert s.competition is None


class TestWorldCompetitionCrossChecks:
    """WorldConfig is where a team's members are checked against the scenario cast
    and team labels against agent names — the rules that need the whole world."""

    def _world(self, competition: dict) -> dict:
        return {
            "agents": [
                {"name": "spy-nil", "persona": "p"},
                {"name": "spy-cara", "persona": "p"},
                {"name": "host", "persona": "p"},
            ],
            "scenarios": [
                {
                    "name": "duel",
                    "default_seed": "seed",
                    "cast": ["spy-nil", "spy-cara", "host"],
                    "competition": competition,
                }
            ],
        }

    def test_coherent_versus_world_validates(self):
        world = validate_world(self._world({"kind": "versus", "teams": {"spy": ["spy-nil"], "herd": ["spy-cara"]}}))
        assert world.scenarios[0].competition.kind == "versus"

    def test_off_cast_team_member_rejected(self):
        # A team naming an agent not in this scenario's cast can never be scored.
        with pytest.raises(ValidationError) as exc:
            validate_world(self._world({"kind": "versus", "teams": {"spy": ["ghost-agent"], "herd": ["spy-cara"]}}))
        assert "members not in its cast" in str(exc.value)
        assert "ghost-agent" in str(exc.value)

    def test_team_label_colliding_with_agent_name_rejected(self):
        # winner carries an agent name OR a team label; a label that IS an agent name
        # makes that union ambiguous, so the cross-cast check must reject it.
        with pytest.raises(ValidationError) as exc:
            validate_world(self._world({"kind": "versus", "teams": {"host": ["spy-nil"], "herd": ["spy-cara"]}}))
        assert "collide with agent names" in str(exc.value)
        assert "host" in str(exc.value)

    def test_judged_scenario_skips_team_checks(self):
        # No teams means the cross-cast loop has nothing to enforce — it must pass.
        world = validate_world(self._world({"kind": "judged"}))
        assert world.scenarios[0].competition.kind == "judged"
