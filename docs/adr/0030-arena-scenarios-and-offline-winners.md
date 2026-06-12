# ADR-0030: Arena Scenarios — Symmetric Seats, Ground-Truth Games, and Offline Winners

## Status

Accepted

## Context

[ADR-0029](0029-structured-verdicts.md) made the winner a first-class, validated
payload field: a judge emits a typed `winner` (and `scores`), the base agent validates
it against the cast with one corrective re-ask, and ground truth is scored in code on
the `versus` path. That gave us the *contract*. This ADR records the work that makes
**every** scenario arena-grade on top of it (Workstream 3 of the
[arena roadmap](../architecture/next-steps/arena-roadmap.md)), and resolves two gaps
ADR-0029 left open:

1. **Comparing models needs symmetric seats.** ADR-0029's `versus` is a contest between
   named `teams`. But the cleanest "which model plays better" arena is two *identical*
   seats that differ only by which model fills them — there is no team, just a duel.
   `teams` could not express that.
2. **The offline stub crowns no one.** ADR-0029 deliberately has the deterministic stub
   emit no `winner` (the field is optional; determinism preserved). That is correct for
   the live path, but it means the no-API-key demo of a *judged* scenario ends with a
   verdict sentence and an empty scoreboard — and the no-key path is a hard product
   constraint (it is what runs on stage). Ground-truth games (The Steeped) already crown
   offline because code decides; judged games did not.

## Decision

### 1. `symmetric_seats` — a versus contest of identical seats

Extend `CompetitionConfig` (ADR-0029 §1) with an alternative to `teams`:

```yaml
competition:
  kind: versus
  symmetric_seats: [debater-a, debater-b]   # identical manifests, different models
```

A `versus` scenario declares *either* `teams` or `symmetric_seats`. The validator
requires ≥2 seats; `WorldConfig` checks the seats are in the cast (alongside the
existing team-membership and label-collision checks). The arena fairness guarantee — the
seats are truly interchangeable apart from the model — is enforced by the contract test
(below), which asserts the seat manifests are identical except `name`, `hue`,
`archetype`, `model_profile`, and `model_endpoint`.

### 2. Offline winner repair — `JudgedCompetition` (`handler: judged-competition`)

A thin handler that runs *after* the base agent's live validation (ADR-0029 §3) and
fills an **empty** winner so the offline demo still crowns one:

- If the verdict already names a real competitor (or team label), keep it.
- Otherwise — the offline stub, which emits no `winner` — recover the winner from the
  verdict *prose* (full-slug match, so symmetric seats `debater-a`/`debater-b` are
  distinguished, earliest-mentioned wins), then fall back to the most active competitor.
  Deterministic, so offline runs stay reproducible.
- It **defers to `no_contest`**: when the engine forfeited the round (a live model that
  twice refused to name a real player), the handler does not manufacture a winner over
  that explicit forfeit.

The judges of the judged / symmetric-seat scenarios (`mystery-judge`, `table-judge`,
`debate-judge`, `beat-judge`) use this handler. It is purely additive to ADR-0029: the
live path is unchanged; only the offline empty-winner case is repaired.

### 3. Ground-truth games where code owns the answer — Twenty Sprouts

`src/agents/twenty_sprouts.py` adds two handlers, mirroring The Steeped's
code-owns-the-truth discipline:

- `SecretKeeper` deals a secret word deterministically from the seed and carries it on a
  **private** `secret` payload key. Because the context/memory builder surfaces only an
  event's `text` (never the raw payload), the guesser never sees the word — it rides the
  ledger as ground truth without leaking.
- `SproutJudge` (a `JudgedCompetition` subclass that overrides `decide_winner` and so
  ignores `no_contest`) reads the dealt word and the guesser's last line off the ledger
  and decides the winner in code — the guesser if the word appears in the final guess,
  else the keeper — attaching a `reveal` that unmasks the word.

### 4. The eight arena-grade scenarios

The five existing scenarios were audited and three competitive scenarios added:

| Scenario | kind | winner decided by |
|---|---|---|
| 🕵 The Steeped | versus (teams) | code — `SpyHost` (ADR-0029 §4) |
| ❓ Twenty Sprouts | versus (teams) | code — `SproutJudge` ground truth |
| ⚔️ Debate Duel | versus (symmetric_seats) | judge + offline repair |
| 🎭 Beat Battle | versus (symmetric_seats) | judge + offline repair |
| 🔍 Mystery Roots | judged | judge + offline repair |
| 💬 Open Table | judged | new `table-judge` + offline repair |
| 🍄 Thousand Token Wood | none | — (collaborative) |
| 🔮 Oracle Grove | none | — (tool-use showcase) |

### 5. UX and enforcement

- A winner ribbon in the verdict banner (`render_verdict`) appears **only** when a
  `winner` is present, so `none`-kind scenarios and legacy runs render byte-identically.
- `tests/test_scenario_contract.py` loads every scenario YAML, composes a `WorldConfig`,
  and asserts the authoring checklist (explicit `competition` block, a judge for
  competitive kinds, team/seat membership, symmetric-seat parity, and a real offline
  winner end-to-end). See `docs/architecture/scenario-authoring.md`.

## Consequences

- Offline judged/seat scenarios now crown a winner, keeping the no-key demo watchable —
  a deliberate, additive deviation from ADR-0029's "offline emits no winner". The
  repaired winner is best-effort (prose/activity), clearly downstream of the model's
  judgement, and never overrides a live `no_contest`.
- `symmetric_seats` gives the model leaderboard its fairest comparison; the contract test
  guards the seats from drifting apart.
- The `competition` block is also stamped onto `run.started` (so a run is
  self-describing for the future leaderboard), complementing ADR-0029's registry-injected
  `agent.competition`.

## References

- [ADR-0029](0029-structured-verdicts.md) — structured verdicts (the foundation).
- [ADR-0011](0011-declarative-validatable-config.md) — declarative validatable config.
- [ADR-0016](0016-instructor-structured-output.md) — structured output.
- [arena roadmap](../architecture/next-steps/arena-roadmap.md) — Workstream 3.
