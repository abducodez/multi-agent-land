"""The scenario authoring checklist, enforced.

Every shipped scenario must be *arena-grade* (ADR-0029): premise + cast + governor +
an explicit ``competition`` block + a real end condition + a structured verdict path.
These tests load every ``config/scenarios/*.yaml`` and assert that contract — entirely
offline, no tokens — so a new scenario can't ship half-wired.  See
``docs/architecture/scenario-authoring.md`` for the human-readable checklist.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.core.config import WorldConfig
from src.core.conductor import Conductor
from src.core.governor import BudgetExceeded
from src.core.registry import Registry
from src.models.router import ModelRouter
from src.tools.builtins import default_tool_registry

_CONFIG = Path(__file__).resolve().parents[1] / "config"
_SCENARIO_FILES = sorted((_CONFIG / "scenarios").glob("*.yaml"))
_SCENARIO_IDS = [p.stem for p in _SCENARIO_FILES]

# Across symmetric seats (Debate Duel, Beat Battle) only these may differ — the whole
# point is "same seat, different model", so everything else must be byte-identical.
_SEAT_DIFF_ALLOWED = {"name", "hue", "archetype", "model_profile", "model_endpoint"}


@pytest.fixture(scope="module")
def registry() -> Registry:
    return Registry.from_dir()


@pytest.fixture(scope="module")
def world(registry: Registry) -> WorldConfig:
    # Composing a WorldConfig is what exercises the cross-cast competition rules
    # (team/seat members ⊆ cast; judged/versus need a judge) — the per-file
    # validate_scenario path can't see the agent set.  Must not raise.
    return WorldConfig(agents=list(registry.agents.values()), scenarios=list(registry.scenarios.values()))


def test_all_scenarios_compose_into_a_valid_world(world: WorldConfig) -> None:
    assert world.scenarios, "expected at least one scenario to load"


@pytest.mark.parametrize("path", _SCENARIO_FILES, ids=_SCENARIO_IDS)
def test_scenario_declares_an_explicit_competition_block(path: Path) -> None:
    """The block is defaulted in the schema, but the checklist requires it spelled out."""
    raw = yaml.safe_load(path.read_text()) or {}
    assert "competition" in raw, f"{path.stem}: must declare an explicit `competition` block"
    assert raw["competition"].get("kind") in ("versus", "judged", "none"), f"{path.stem}: bad competition.kind"


@pytest.mark.parametrize("path", _SCENARIO_FILES, ids=_SCENARIO_IDS)
def test_scenario_has_the_authoring_basics(path: Path, registry: Registry) -> None:
    """Premise + cast + governor — the bones every scenario needs."""
    cfg = registry.scenarios[path.stem]
    assert cfg.goal.strip(), f"{path.stem}: needs a goal"
    assert cfg.default_seed.strip(), f"{path.stem}: needs a default_seed"
    assert cfg.example_seeds, f"{path.stem}: needs example_seeds"
    assert cfg.genesis_text, f"{path.stem}: needs genesis_text"
    assert cfg.governor is not None, f"{path.stem}: needs an explicit governor budget"
    assert cfg.cast, f"{path.stem}: needs a cast"


@pytest.mark.parametrize("path", _SCENARIO_FILES, ids=_SCENARIO_IDS)
def test_competitive_scenarios_have_a_judge_that_ends_the_show(path: Path, registry: Registry) -> None:
    """judged/versus need a judge emitting judge.verdict, on a tick that fires in-budget.

    ``none`` scenarios must NOT be forced to carry a judge.
    """
    cfg = registry.scenarios[path.stem]
    comp = cfg.competition
    judges = [
        m
        for name in cfg.cast
        if (m := registry.agents.get(name)) and m.role == "judge" and "judge.verdict" in m.may_emit
    ]
    if comp.kind == "none":
        return  # showcase / collaborative — no winner machinery required
    assert judges, f"{path.stem}: kind {comp.kind!r} needs a judge emitting judge.verdict"
    # End condition: at least one judge fires within the round budget, so the verdict
    # actually lands instead of the show grinding to a budget halt with no winner.
    max_turns = cfg.governor.max_turns if cfg.governor else 100
    firing = [m for m in judges if m.schedule.tick_every is not None and 0 < m.schedule.tick_every <= max_turns]
    assert firing, f"{path.stem}: no judge has a tick_every that fires within max_turns={max_turns}"


@pytest.mark.parametrize("path", _SCENARIO_FILES, ids=_SCENARIO_IDS)
def test_symmetric_seats_are_identical_except_model(path: Path, registry: Registry) -> None:
    """Debate Duel / Beat Battle: symmetric seats may differ only by name/hue/model.

    This is the fairness guarantee for the model leaderboard — the seats must be
    truly identical apart from which model fills them.
    """
    cfg = registry.scenarios[path.stem]
    seats = cfg.competition.symmetric_seats or []
    if len(seats) < 2:
        return
    manifests = [registry.agents[name].model_dump() for name in seats]
    base = manifests[0]
    for other in manifests[1:]:
        differing = {k for k in base if base[k] != other[k]}
        illegal = differing - _SEAT_DIFF_ALLOWED
        assert not illegal, (
            f"{path.stem}: symmetric seats {seats} differ in fields they may not: {sorted(illegal)} "
            f"(only {sorted(_SEAT_DIFF_ALLOWED)} may differ)"
        )


def test_world_rejects_team_member_not_in_cast(registry: Registry) -> None:
    """The cross-cast guard must actually fire on a broken competition block."""
    good = registry.scenarios["the-steeped"].model_dump()
    good["competition"] = {"kind": "versus", "teams": {"spy": ["nobody-here"], "herd": ["spy-cara"]}}
    with pytest.raises(ValueError, match="non-cast members"):
        WorldConfig(agents=list(registry.agents.values()), scenarios=[good])


def test_world_rejects_competitive_scenario_without_a_judge(registry: Registry) -> None:
    bad = registry.scenarios["open-table"].model_dump()
    bad["cast"] = ["chat-curious", "chat-skeptic", "chat-host"]  # drop the table-judge
    bad["competition"] = {"kind": "judged"}
    with pytest.raises(ValueError, match="requires a cast member"):
        WorldConfig(agents=list(registry.agents.values()), scenarios=[bad])


@pytest.mark.parametrize("name", ["the-steeped", "mystery-roots", "debate-duel", "twenty-sprouts"])
def test_competitive_scenario_names_a_real_winner_offline(name: str, registry: Registry) -> None:
    """End-to-end on the deterministic stub: the verdict names a real player / team.

    Proves the watchable-stub winner path (handler validation + fallback) without
    spending a token, and that winner attribution is assertable on the offline path.
    """
    scenario = registry.build_scenario(name, router=ModelRouter(offline=True, specs={}), tools=default_tool_registry())
    conductor = Conductor(scenario, governor=registry.governor_for(name))
    conductor.reset(scenario.default_seed)
    for _ in range(80):
        try:
            conductor.step()
        except BudgetExceeded:
            break
        if any(e.kind == "judge.verdict" for e in conductor.ledger.events_for_run(conductor.run_id)):
            break
    events = conductor.ledger.events_for_run(conductor.run_id)
    verdict = next((e for e in reversed(events) if e.kind == "judge.verdict"), None)
    assert verdict is not None, f"{name}: no verdict reached offline"
    winner = verdict.payload.get("winner")
    cfg = registry.scenarios[name]
    valid = set(cfg.cast) | set(cfg.competition.teams or {})
    assert winner in valid, f"{name}: winner {winner!r} is not a cast member or team label"
