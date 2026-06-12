# ADR-0029: The Scenario Competition Contract — Winners as Data

## Status

Accepted

## Context

The arena vision (the [arena roadmap](../architecture/next-steps/arena-roadmap.md),
Workstreams 2–3) needs one answer the engine could not give: **who won, and on which
model?** Three gaps blocked it:

1. **Winners were prose.** A `judge.verdict` carried only text (`"Verdict: …"`).
   ADR-0026's `run.finished` reserved a `winner` field, but nothing guaranteed a
   structured winner ever existed to fill it — every consumer would have had to parse
   the judge's sentence.
2. **Some scenarios had no judge at all.** Open Table and Oracle Grove ran until the
   budget tripped; nothing distinguished "this show crowns a winner" from "this show
   is a collaborative showcase".
3. **The leaderboard (W6) needs declarative attribution.** Wins must attribute to a
   *model*, but a winner can be an agent, a lone-member team (The Steeped's spy), or a
   seat that several minds share. Which scenarios produce winners, and how to map a
   winner to a model, had to be data the run carries — not a heuristic.

## Decision

Make every scenario declare how (and whether) it produces a winner, and make every
winner a machine-readable cast reference. Four pieces:

- **A `competition` block on `ScenarioConfig`**, validated by a new
  `CompetitionConfig` Pydantic model (`src/core/config.py`, the ADR-0011 declarative
  surface):

  ```yaml
  competition:
    kind: versus | judged | none
    teams: {spy: [spy-nil], herd: [spy-cara, spy-bex, spy-ovo]}  # versus, asymmetric sides
    symmetric_seats: [debater-a, debater-b]                       # versus, identical seats
  ```

  `none` = collaborative/showcase — no winner, no leaderboard rows, still a full
  session. `judged` = a judge in the cast names the winning *agent*. `versus` =
  head-to-head — the winner is decided by ground-truth code or by a judge naming a
  side. `symmetric_seats` are identical manifests bound to different models — the
  "which model argues better" comparison that makes the model leaderboard meaningful.

  Validation splits by what it needs to see. Shape rules are self-contained on
  `CompetitionConfig._check_kind_shape` (`none` forbids teams/seats; `versus` needs
  ≥2 teams or ≥2 seats). Cross-cast rules live in `WorldConfig._check_competition`
  (team/seat members ⊆ cast; `versus`/`judged` require a cast member with
  `role: judge` that emits `judge.verdict`). The block defaults to `kind: none` so a
  block-less scenario still validates; the authoring checklist and
  `tests/test_scenario_contract.py` require an explicit block on every shipped
  scenario.

- **The block is stamped onto `run.started`** (`Conductor.reset()`,
  `src/core/conductor.py`), next to ADR-0026's cast→model map. A run is
  self-describing forever: the leaderboard and `FishbowlSession.finalize` read the
  contract off the event, never off mutable config.

- **AI judges, code scores.** Judges list `winner` in `output_extra_fields`
  (ADR-0016 structured output), so the live model must emit one. The
  `JudgedCompetition` handler (`handler: judged-competition`,
  `src/agents/competition.py`) then guarantees it is real: it validates the name
  against the agents who actually played and, on a miss (offline stub, or a live
  hallucination), repairs it deterministically — a name found in the verdict prose
  first, the most-active competitor as the fallback. The winner is *always* a genuine
  player, so the no-API-key demo stays watchable. Where ground truth exists, code
  decides instead: `SproutJudge` (`src/agents/twenty_sprouts.py`) subclasses
  `JudgedCompetition` and overrides `decide_winner` — it reads the dealt secret word
  off the ledger and checks the guesser's final line. `SpyHost`
  (`src/agents/handlers.py`) parses the accused name, compares it to the actual spy,
  and stamps `winner = "herd" | "spy"` (a *team* label) plus `correct: bool`. The LLM
  provides the drama; the handler provides the scoreboard.

- **Attribution reconciles agent names and team labels.**
  `FishbowlSession.finalize` (`src/ui/fishbowl/session.py`) maps an agent-name winner
  straight to its model via the `run.started` cast map; a single-member team
  attributes the lone member's model; a multi-member team yields no single winning
  model — the seat won, not a model. The Fishbowl shows a winner ribbon in the
  verdict banner (`view_model.py` → `render/meters.py::render_verdict`) only when a
  `winner` is present, so `none`-kind scenarios and legacy runs render byte-identically
  to before.

All eight shipped scenarios now declare their kind: The Steeped (versus,
teams, ground-truth `SpyHost`) · Twenty Sprouts (versus, teams, ground-truth
`SproutJudge`) · Debate Duel and Beat Battle (versus, symmetric seats) · Mystery
Roots and Open Table (judged) · Thousand Token Wood and Oracle Grove (none).

## Consequences

- **The leaderboard becomes a projection.** `run.started` carries the contract and
  the cast→model map; `judge.verdict` carries a validated winner; `run.finished`
  carries the resolved `winner`/`winning_model`. W6 is a pure fold over the ledger.
- **Offline runs stay reproducible and watchable.** The deterministic repair path
  means the stub demo crowns a real player without any model emitting a `winner`.
- **Scenarios are honest about what they are.** `kind: none` is a first-class
  answer — Oracle Grove no longer looks like a game that forgot its judge.
- **Cross-cast validation only runs through `WorldConfig`.** `validate_scenario` on a
  lone YAML checks shape but cannot know the cast's manifests, so a missing judge or
  stray team member surfaces only when a `WorldConfig` is composed — the Lab path and
  `tests/test_scenario_contract.py`, not per-file validation. Authors must run the
  contract test, not just eyeball the YAML.
- **The repaired winner can be wrong — but never fake.** If a live judge hallucinates
  a name and the prose match fails, the most-active fallback may crown a competitor
  the judge did not intend. We trade occasional misattribution for a hard guarantee
  that every winner maps to a real model.
- **`winner` is overloaded: agent name or team label.** Consumers must check both
  namespaces (as `finalize` does). A future agent named like a team label would
  collide; cast naming conventions carry that risk for now.
- **Symmetric-seat fairness is deferred.** Raw win counts mislead when seats differ
  in difficulty or order; the model leaderboard should report win rate *per seat* and
  alternate first-speaker across sessions (roadmap §6.3). The contract records the
  seats; the fairness math lands with W6.

## Alternatives considered

- **Parse winners out of verdict prose at read time.** No schema change, but every
  consumer re-implements the heuristic, the offline stub yields no winner at all, and
  a hallucinated name poisons history silently. Rejected: the ledger should store the
  answer, not the puzzle.
- **Require a judge in every scenario.** Uniform, but forces a fake contest onto
  collaborative showcases. `kind: none` keeps them honest and still gives them
  sessions and history.
- **Re-ask the model on an invalid winner, then declare "no contest"** (the
  roadmap's original sketch). Spends live budget on a retry and leaves offline runs
  winner-less. The deterministic repair is cheaper and keeps every demo resolvable.
- **Hang the contract on the judge manifest instead of the scenario.** The judge
  doesn't know the teams or the cast — the scenario does. Keeping it on
  `ScenarioConfig` also lets one generic judge handler (`judged-competition`) serve
  four different scenarios.
- **Make the `competition` block required.** Breaks every legacy/test scenario and
  Lab-composed world. A `kind: none` default plus test-enforced explicitness on
  shipped scenarios gets the same guarantee without the migration.

## References

- Builds on ADR-0011 (declarative, validatable config — `CompetitionConfig` is that
  surface), ADR-0016 (validated structured output — `winner` rides
  `output_extra_fields`), and ADR-0026 (run lifecycle — `run.started` stamping,
  `run.finished.winner`/`winning_model`).
- Spec: [arena-roadmap.md](../architecture/next-steps/arena-roadmap.md) §W2–W3;
  authoring guide: [scenario-authoring.md](../architecture/scenario-authoring.md)
  (the arena-grade checklist).
- `src/core/config.py` — `CompetitionConfig`, `ScenarioConfig.competition`,
  `WorldConfig._check_competition`
- `src/core/conductor.py` — `run.started` competition stamp
- `src/agents/competition.py` — `JudgedCompetition`; `src/agents/handlers.py` —
  `SpyHost`; `src/agents/twenty_sprouts.py` — `SecretKeeper`, `SproutJudge`
- `src/ui/fishbowl/session.py` — winner→model reconciliation;
  `src/ui/fishbowl/view_model.py`, `src/ui/fishbowl/render/meters.py` — winner ribbon
- `tests/test_scenario_contract.py` — the enforced authoring checklist
