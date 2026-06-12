"""Declarative configuration schemas — config as validatable data.

These Pydantic models are the contract for the 'easily configurable' surface.
Every knob — which agents exist, what they emit, which model tier they use, the
scenario goal, the cast that participates, tool grants, budgets — is expressed as
data that:

  * round-trips to/from YAML files under ``config/`` (the on-disk surface), and
  * can equally be produced by a UI form or an LLM and checked with one call.

That last property is the point: ``validate_world`` / ``validate_agent`` /
``validate_scenario`` turn an arbitrary dict into a typed, cross-checked object or
a precise error.  So "let an agent build the configuration from a prompt" reduces
to "emit JSON, validate it, run it."  See ADR-0011.

The agent schema itself is :class:`AgentManifest` (``src/core/manifest.py``) — we
reuse it here rather than duplicating, so the four stable contracts stay singular.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.core.manifest import AgentManifest

# ── model profiles ─────────────────────────────────────────────────────────────


class ModelProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    model: str
    base_url: str | None = None
    """OpenAI-compatible endpoint URL (ends in /v1).  Env-templatable in YAML via
    ``${MODAL_LLM_BASE_URL}`` so the Modal workspace is never hard-coded."""

    api_key: str | None = None
    """Key for the endpoint (env-templatable, e.g. ``${MODAL_LLM_KEY}``).  vLLM
    accepts any token unless the server enforces one."""

    temperature: float = 0.8
    max_tokens: int = 256

    @model_validator(mode="after")
    def _blank_to_none(self) -> "ModelProfileConfig":
        # An unset ``${VAR}`` template expands to "" (see registry._expand_env);
        # normalise empty bindings back to None so the live transport omits them.
        if not self.base_url:
            self.base_url = None
        if not self.api_key:
            self.api_key = None
        return self


class ModelsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offline: bool | None = None
    """True = always use the deterministic stub; False = always live; None = auto
    (live when credentials are present, stub otherwise)."""

    profiles: dict[str, ModelProfileConfig] = Field(default_factory=dict)
    """Concrete binding per logical profile (tiny/fast/balanced/strong)."""


# ── budgets ─────────────────────────────────────────────────────────────────────


class GovernorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_turns: int = 100
    max_calls_per_turn: int = 8
    max_total_calls: int = 500
    max_total_tokens: int | None = None
    hourly_budget_usd: float | None = None


# ── competition ──────────────────────────────────────────────────────────────────

CompetitionKind = Literal["versus", "judged", "none"]


class CompetitionConfig(BaseModel):
    """How (and whether) a scenario produces a winner — the arena contract (ADR-0029).

    Three kinds:
      * ``versus`` — a head-to-head between symmetric seats or named teams; the
        winner is decided either by ground-truth code (The Steeped, Twenty Sprouts)
        or by a judge naming a side.  Use ``teams`` for asymmetric sides and
        ``symmetric_seats`` for "same manifest, different model" duels (Debate Duel,
        Beat Battle) — the latter is what makes the model-leaderboard meaningful.
      * ``judged`` — a judge in the cast names the winning *agent* via its
        ``judge.verdict`` payload ``winner`` field (Mystery Roots, Open Table).
      * ``none`` — collaborative or showcase; no winner, no leaderboard rows, but
        still a full session/history (Thousand Token Wood, Oracle Grove).

    The block is stamped onto ``run.started`` so a run is self-describing forever —
    the leaderboard (W6) reads it to know which runs produce winners and how to
    attribute them.  Team / seat members are validated against the scenario cast in
    :meth:`WorldConfig._check_cast_references`.
    """

    model_config = ConfigDict(extra="forbid")

    kind: CompetitionKind = "none"

    teams: dict[str, list[str]] | None = None
    """Named sides, e.g. ``{spy: [spy-nil], herd: [spy-cara, spy-bex, spy-ovo]}``.
    Each member must be in the scenario cast.  ``versus`` only."""

    symmetric_seats: list[str] | None = None
    """Cast members occupying *identical* seats that differ only by model — the
    "which model argues better" comparison.  ``versus`` only; needs ≥2 entries."""

    @model_validator(mode="after")
    def _check_kind_shape(self) -> "CompetitionConfig":
        if self.kind == "none":
            if self.teams or self.symmetric_seats:
                raise ValueError("competition.kind 'none' must not declare teams or symmetric_seats")
        elif self.kind == "versus":
            n_teams = len(self.teams or {})
            n_seats = len(self.symmetric_seats or [])
            if n_teams < 2 and n_seats < 2:
                raise ValueError(
                    "competition.kind 'versus' needs ≥2 teams or ≥2 symmetric_seats "
                    f"(got teams={n_teams}, symmetric_seats={n_seats})"
                )
        # 'judged' carries no required structural fields — the judge names the winner.
        return self


# ── scenario ─────────────────────────────────────────────────────────────────────


class ScenarioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    title: str = ""
    goal: str = ""
    """The shared objective handed to the whole cast (rendered into every prompt)."""

    default_seed: str
    example_seeds: list[str] = Field(default_factory=list)

    cast: list[str] = Field(default_factory=list)
    """Agent names that participate, resolved against the agent registry.
    Selecting who participates is just editing this list."""

    genesis_text: str | None = None
    governor: GovernorConfig | None = None

    competition: CompetitionConfig = Field(default_factory=CompetitionConfig)
    """The arena contract: how this scenario produces a winner (ADR-0029).  Defaulted
    to ``kind: none`` so a scenario without a block still validates, but the authoring
    checklist (and ``tests/test_scenario_contract.py``) requires an explicit block on
    every shipped scenario."""


# ── the whole world ──────────────────────────────────────────────────────────────


class WorldConfig(BaseModel):
    """A complete, self-contained, validatable description of a runnable world.

    A UI or an LLM can emit one of these (agents + scenarios + models + budgets
    inline) and ``validate_world`` confirms it is coherent before anything runs —
    including that every scenario's cast references a defined agent.
    """

    model_config = ConfigDict(extra="forbid")

    models: ModelsConfig = Field(default_factory=ModelsConfig)
    governor: GovernorConfig = Field(default_factory=GovernorConfig)
    agents: list[AgentManifest] = Field(default_factory=list)
    scenarios: list[ScenarioConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_cast_references(self) -> "WorldConfig":
        by_name = {a.name: a for a in self.agents}
        defined = set(by_name)
        for scenario in self.scenarios:
            missing = [name for name in scenario.cast if name not in defined]
            if missing:
                raise ValueError(
                    f"scenario {scenario.name!r} references undefined agents: {missing}. "
                    f"Defined agents: {sorted(defined)}"
                )
            self._check_competition(scenario, by_name)
        return self

    @staticmethod
    def _check_competition(scenario: ScenarioConfig, by_name: dict[str, AgentManifest]) -> None:
        """Cross-cast rules for the competition contract (ADR-0029).

        Self-contained shape rules live on :class:`CompetitionConfig`; the checks that
        need the cast + agent registry live here: team/seat members must be in the
        cast, and a winner-bearing kind (versus / judged) must include a judge that
        actually emits ``judge.verdict``.  ``none`` scenarios require no judge.
        """
        comp = scenario.competition
        cast = set(scenario.cast)
        members: list[str] = list(scenario.competition.symmetric_seats or [])
        for team in (comp.teams or {}).values():
            members.extend(team)
        stray = sorted(m for m in members if m not in cast)
        if stray:
            raise ValueError(
                f"scenario {scenario.name!r} competition references non-cast members: {stray}. Cast: {sorted(cast)}"
            )
        if comp.kind in ("versus", "judged"):
            judges = [
                name
                for name in scenario.cast
                if (m := by_name.get(name)) and m.role == "judge" and "judge.verdict" in m.may_emit
            ]
            if not judges:
                raise ValueError(
                    f"scenario {scenario.name!r} competition.kind {comp.kind!r} requires a cast member "
                    "with role 'judge' that emits 'judge.verdict' to decide the winner"
                )


# ── validation entrypoints (the 'configure from a prompt' surface) ───────────────


def validate_agent(data: dict) -> AgentManifest:
    """Validate one agent definition (e.g. UI form output or LLM-proposed agent)."""
    return AgentManifest.model_validate(data)


def validate_scenario(data: dict) -> ScenarioConfig:
    """Validate one scenario definition."""
    return ScenarioConfig.model_validate(data)


def validate_world(data: dict) -> WorldConfig:
    """Validate a whole world (agents + scenarios + models + budgets) at once."""
    return WorldConfig.model_validate(data)
