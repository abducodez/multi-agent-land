"""Leaderboard — competitive-results aggregations over the dedicated scoreboard table.

This module is the *read model* of the Hall of Fame.  It is deliberately **detached from
the event ledger**: it folds a list of :class:`~src.core.leaderboard_store.LeaderboardEntry`
rows — the materialised scoreboard persisted in the ``leaderboard_entries`` table — into
the model / agent / fairness tables the UI renders.  It never touches the ``events`` log.

The split, in one line: :mod:`src.core.leaderboard_store` owns *persistence* (one durable
row per decided run, written at finish), and this module owns *aggregation* (cheap folds
over those rows).  Because a row is only ever written for a finished, won, competitive run
(see ``build_entry`` and ``FishbowlSession.finalize``), the functions here can trust every
entry they receive is already "ranked" — they re-check ``winner`` only defensively.

Each entry is self-describing: it carries the cast→model bindings *and* the competition
shape (``competition_kind`` / ``teams`` / ``symmetric_seats``), so the per-seat fairness
rollup needs no registry lookup and no event replay.

Schema is additive only; ``schema_version`` is unaffected (this reads a separate table).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from src.core.leaderboard_store import LeaderboardEntry


# ── competition shape (built per entry for seat mapping) ─────────────────────────


class CompetitionBlock(BaseModel):
    """The competition shape of one run (built from a :class:`LeaderboardEntry`).

    ``kind`` is ``"versus"`` / ``"judged"`` / ``"none"``; ``teams`` (versus only) maps a
    team label → member agent names; ``symmetric_seats`` (versus only) lists identical
    seats that differ only by model.
    """

    model_config = ConfigDict(extra="ignore")

    kind: str = "none"
    teams: dict[str, list[str]] | None = None
    symmetric_seats: list[str] | None = None


def _block(entry: LeaderboardEntry) -> CompetitionBlock:
    """The competition shape carried by *entry* (no registry / ledger lookup)."""
    return CompetitionBlock(
        kind=entry.competition_kind,
        teams=entry.teams,
        symmetric_seats=entry.symmetric_seats,
    )


# ── row models ─────────────────────────────────────────────────────────────────


class ModelRow(BaseModel):
    """A model endpoint's record across *all* competitive decided runs."""

    model_config = ConfigDict(extra="forbid")

    model: str
    plays: int = 0
    """Decided competitive runs whose cast contained this endpoint."""
    wins: int = 0
    """Of those, how many this endpoint was credited with winning."""
    win_rate: float = 0.0
    """``wins / plays`` (0.0 when ``plays == 0``)."""
    scenarios: list[str] = Field(default_factory=list)
    """Sorted distinct scenario names this endpoint appeared in."""


class AgentRow(BaseModel):
    """A persona's (cast seat *name*) record within a single scenario."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    """The cast member name — the seat, not the model."""
    seat_type: str = ""
    """Team label, symmetric-seat name, or ``""`` when the seat maps to neither."""
    plays: int = 0
    wins: int = 0
    win_rate: float = 0.0
    model_endpoints: list[str] = Field(default_factory=list)
    """Sorted distinct model endpoints that filled this seat."""


class SeatRow(BaseModel):
    """Win rate per *seat type* within a scenario (the 6.3 fairness footnote).

    Surfaces structural asymmetry: spy vs herd, debater-a vs debater-b, the judge that
    never wins.  ``seat_type`` is a team label (versus-teams) or a symmetric-seat name.
    """

    model_config = ConfigDict(extra="forbid")

    seat_type: str
    plays: int = 0
    wins: int = 0
    win_rate: float = 0.0


# ── internal helpers ───────────────────────────────────────────────────────────


def _win_rate(wins: int, plays: int) -> float:
    """``wins / plays``, or ``0.0`` when *plays* is zero (never divide by zero)."""
    return (wins / plays) if plays else 0.0


def _ranked(entries: Iterable[LeaderboardEntry]) -> list[LeaderboardEntry]:
    """Defensive gate: keep only entries that actually name a winner.

    The store only ever persists finished + won + competitive runs, so this is belt-and-
    suspenders — it drops any malformed/empty row rather than crediting a phantom win.
    """
    return [e for e in entries if e and e.winner]


def _seat_type_for(agent: str, block: CompetitionBlock) -> str:
    """Map a cast member *name* to its seat type within *block*.

    For ``versus`` teams the seat type is the team label the agent belongs to.  For
    ``symmetric_seats`` each named seat is its own type (the agent name == the seat).  A
    cast member that belongs to neither (e.g. a judge, a narrator) maps to ``""``.
    """
    if block.teams:
        for label, members in block.teams.items():
            if agent in (members or []):
                return label
    if block.symmetric_seats and agent in block.symmetric_seats:
        return agent
    return ""


def _winning_seat_types(entry: LeaderboardEntry, block: CompetitionBlock) -> set[str]:
    """The seat type(s) credited with the win for fairness accounting."""
    winner = entry.winner or ""
    if not winner:
        return set()
    if block.teams and winner in block.teams:
        return {winner}
    seat = _seat_type_for(winner, block)
    return {seat} if seat else set()


def _winning_agents(entry: LeaderboardEntry, block: CompetitionBlock) -> set[str]:
    """Cast member name(s) credited with the win, for per-seat accounting.

    A ``team`` winner credits every member of the winning team; an ``agent`` winner
    (judged pick / symmetric-seat winner) credits just that name.  Falls back to treating
    ``winner`` as a bare agent name when ``winner_kind`` is absent.
    """
    winner = entry.winner or ""
    if not winner:
        return set()
    if entry.winner_kind == "team" or (block.teams and winner in block.teams):
        return set((block.teams or {}).get(winner) or [])
    return {winner}


def _credited_models(entry: LeaderboardEntry) -> set[str]:
    """The model endpoint(s) credited with this run's win (``winning_models`` ∪ single)."""
    credited = {m for m in entry.winning_models if m}
    if entry.winning_model:
        credited.add(entry.winning_model)
    return credited


# ── public aggregations (fold the scoreboard rows) ───────────────────────────────


def scenario_sessions(entries: Sequence[LeaderboardEntry], scenario_name: str) -> list[LeaderboardEntry]:
    """The decided sessions of *scenario_name*, newest first.

    A thin filter + deterministic sort over the stored rows: by ``finished_at``
    descending (newest first), runs missing a finish time sorted last, ``run_id`` breaking
    ties.  Returns the :class:`LeaderboardEntry` rows themselves — they already carry the
    winner, cast→model bindings and cost the sessions table renders.
    """
    rows = [e for e in _ranked(entries) if e.scenario == scenario_name]
    rows.sort(key=lambda e: (e.finished_at is None, _neg_ts(e.finished_at), e.run_id))
    return rows


def model_table(entries: Sequence[LeaderboardEntry]) -> list[ModelRow]:
    """One :class:`ModelRow` per model endpoint across *all* decided competitive runs.

    A model *plays* a run when its endpoint appears in that run's cast; it *wins* when its
    endpoint is among the run's credited winners (``winning_models`` / ``winning_model``).
    ``scenarios`` lists the distinct scenario names the model appeared in (sorted).  Sorted
    by ``win_rate`` desc, then ``wins`` desc, then ``plays`` desc, then ``model`` asc.
    """
    plays: dict[str, int] = defaultdict(int)
    wins: dict[str, int] = defaultdict(int)
    scenarios: dict[str, set[str]] = defaultdict(set)
    for entry in _ranked(entries):
        credited = _credited_models(entry)
        seen: set[str] = set()
        for binding in entry.cast.values():
            endpoint = binding.model_endpoint
            if not endpoint or endpoint in seen:
                continue  # one play per endpoint per run, even if it fills two seats
            seen.add(endpoint)
            plays[endpoint] += 1
            scenarios[endpoint].add(entry.scenario)
            if endpoint in credited:
                wins[endpoint] += 1
    rows = [
        ModelRow(
            model=endpoint,
            plays=plays[endpoint],
            wins=wins[endpoint],
            win_rate=_win_rate(wins[endpoint], plays[endpoint]),
            scenarios=sorted(scenarios[endpoint]),
        )
        for endpoint in plays
    ]
    rows.sort(key=lambda r: (-r.win_rate, -r.wins, -r.plays, r.model))
    return rows


def agent_table(entries: Sequence[LeaderboardEntry], scenario_name: str) -> list[AgentRow]:
    """Per-persona (cast seat *name*) wins within *scenario_name*.

    One :class:`AgentRow` per cast member name that appears in a decided run of the
    scenario.  A seat *plays* a run when its name is in the cast and *wins* when it is the
    run's winning agent, or a member of the winning team.  ``seat_type`` is the seat's team
    label / symmetric-seat name (or ``""``); ``model_endpoints`` lists the distinct models
    that filled the seat.  Deterministic sort matching :func:`model_table`.
    """
    plays: dict[str, int] = defaultdict(int)
    wins: dict[str, int] = defaultdict(int)
    seat_types: dict[str, str] = {}
    endpoints: dict[str, set[str]] = defaultdict(set)
    for entry in _ranked(entries):
        if entry.scenario != scenario_name:
            continue
        block = _block(entry)
        winners = _winning_agents(entry, block)
        for name, binding in entry.cast.items():
            plays[name] += 1
            seat_types.setdefault(name, _seat_type_for(name, block))
            if binding.model_endpoint:
                endpoints[name].add(binding.model_endpoint)
            if name in winners:
                wins[name] += 1
    rows = [
        AgentRow(
            agent=name,
            seat_type=seat_types.get(name, ""),
            plays=plays[name],
            wins=wins[name],
            win_rate=_win_rate(wins[name], plays[name]),
            model_endpoints=sorted(endpoints[name]),
        )
        for name in plays
    ]
    rows.sort(key=lambda r: (-r.win_rate, -r.wins, -r.plays, r.agent))
    return rows


def fairness_table(entries: Sequence[LeaderboardEntry], scenario_name: str) -> list[SeatRow]:
    """Win rate per *seat type* within *scenario_name* — the 6.3 fairness footnote.

    Aggregates the per-persona view up to seat types so structural asymmetry is visible:
    spy vs herd, debater-a vs debater-b, a judge that never wins.  Seat membership comes
    from each entry's stored competition shape (``teams`` → label per member;
    ``symmetric_seats`` → each seat its own type).  A run contributes one *play* to each
    seat type present in its cast, and one *win* to whichever seat type the winner maps to.
    Unmapped cast members (``seat_type == ""``) are not counted — only declared seats
    appear.  Sorted by ``win_rate`` desc, ``wins`` desc, ``plays`` desc, ``seat_type`` asc.
    """
    plays: dict[str, int] = defaultdict(int)
    wins: dict[str, int] = defaultdict(int)
    for entry in _ranked(entries):
        if entry.scenario != scenario_name:
            continue
        block = _block(entry)
        seats_present = {st for st in (_seat_type_for(n, block) for n in entry.cast) if st}
        for seat in seats_present:
            plays[seat] += 1
        for seat in _winning_seat_types(entry, block):
            if seat in seats_present:
                wins[seat] += 1
    rows = [
        SeatRow(
            seat_type=seat,
            plays=plays[seat],
            wins=wins[seat],
            win_rate=_win_rate(wins[seat], plays[seat]),
        )
        for seat in plays
    ]
    rows.sort(key=lambda r: (-r.win_rate, -r.wins, -r.plays, r.seat_type))
    return rows


def headline(entries: Sequence[LeaderboardEntry]) -> str | None:
    """The killer demo line, or ``None`` when there isn't enough data.

    Looks for the most-played *symmetric-seat* scenario (the "which model argues better"
    comparison) and, within it, the two models with the most head-to-head wins.  Renders
    e.g. ``"MiniCPM-8B beats Gemma-12B · 7-3 at Debate Duel"``.  Returns ``None`` when no
    competitive symmetric scenario has at least two distinct models that have each won at
    least once (so the line is never a hollow "0-0").
    """
    ranked = _ranked(entries)
    if not ranked:
        return None

    by_scenario: dict[str, list[LeaderboardEntry]] = defaultdict(list)
    for entry in ranked:
        if entry.symmetric_seats:  # the model-vs-model comparison only
            by_scenario[entry.scenario].append(entry)
    if not by_scenario:
        return None

    best_line: str | None = None
    best_key: tuple[int, int] = (-1, -1)
    for scenario in sorted(by_scenario):  # ascending scan: ties resolve to the first (alphabetical) name
        runs = by_scenario[scenario]
        wins: dict[str, int] = defaultdict(int)
        plays: dict[str, int] = defaultdict(int)
        for entry in runs:
            credited = _credited_models(entry)
            for endpoint in {b.model_endpoint for b in entry.cast.values() if b.model_endpoint}:
                plays[endpoint] += 1
                if endpoint in credited:
                    wins[endpoint] += 1
        winners = sorted((m for m in plays if wins[m] > 0), key=lambda m: (-wins[m], -plays[m], m))
        if len(winners) < 2:
            continue
        top, runner = winners[0], winners[1]
        decided = wins[top] + wins[runner]
        candidate_key = (decided, wins[top])
        if candidate_key > best_key:  # strict: a full tie keeps the earlier (alphabetical) scenario
            best_key = candidate_key
            best_line = f"{_short(top)} beats {_short(runner)} · {wins[top]}-{wins[runner]} at {scenario}"
    return best_line


# ── tiny formatting helpers ─────────────────────────────────────────────────────


def _neg_ts(value: datetime | None) -> float:
    """Negated POSIX timestamp for descending sort; ``0.0`` for ``None`` (sorted last)."""
    return -value.timestamp() if value is not None else 0.0


def _short(endpoint: str) -> str:
    """Compact a model endpoint for the headline (``"openai/openbmb/X"`` → ``"X"``)."""
    return endpoint.rsplit("/", 1)[-1] if endpoint else endpoint


__all__ = [
    "AgentRow",
    "CompetitionBlock",
    "ModelRow",
    "SeatRow",
    "agent_table",
    "fairness_table",
    "headline",
    "model_table",
    "scenario_sessions",
]
