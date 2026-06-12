# ADR-0029: Structured Verdicts — Winners as Data, Not Prose

## Status

Proposed

## Context

A `judge.verdict` today is prose with a `mood`. The run lifecycle (ADR-0026) already
*wants* a machine-readable winner — `FishbowlSession.finalize` reads
`verdict.payload.get("winner")` and `RunSummary` carries `winner` / `winning_model` —
but nothing reliably populates that key: judges emit `{kind, text, mood}` and the
winner lives only in the sentence "Verdict: Bex slipped the tell." A leaderboard, a
shareable trace with a scoreboard, and any "who won on what model" claim all need the
winner as data.

Three constraints shape the design:

1. **The structured-output contract is string-typed.** `build_output_model`
   (`src/core/structured.py`, ADR-0016) makes every `output_extra_fields` entry a
   *required `str`*. A winner is an optional cast name; scores are a `dict[str, float]`.
   The offline tolerant parser already passes arbitrary JSON keys through untouched,
   so only the typed live model and the prompt instruction are string-bound.
2. **Agents never see scenario config.** `Registry.build_scenario` resolves the cast
   *before* constructing the `Scenario`; a judge's handler has no view of which
   scenario it serves, so it cannot know the cast to validate against or the team map
   to attribute with — unless the registry injects that context (the same seam as
   `agent.manifest = manifest`).
3. **Some scenarios have ground truth, some don't, some have neither.** In The Steeped
   the engine *knows* the spy (`spy-nil`) — the judge's accusation can be scored by
   code. In Mystery Roots there is no truth; the judge's pick *is* the answer. In
   Thousand Token Wood nothing wins. One mechanism must serve all three without
   hard-coding scenario names in the engine.

Prize relevance: a code-stamped scoreboard over an LLM verdict is the cleanest
"AI is load-bearing for judgment, code is load-bearing for bookkeeping" story
(Best Agent, Best Demo), and the enriched trace strengthens the Sharing-is-Caring
export (ADR-0026).

## Decision

Make the winner a first-class, validated payload field, derived by the layer that
actually knows it: the LLM where judgment is the product, the handler where ground
truth exists, and never at all where the scenario declares no competition. Five
additive pieces, `schema_version` stays 1 (ADR-0009).

### 1. Scenario competition contract (`CompetitionConfig`, extends ADR-0011)

`ScenarioConfig` gains an optional block, validated in `src/core/config.py`:

```yaml
competition:
  kind: versus | judged | none      # default none; absent block == none
  teams:                            # versus only
    spy: [spy-nil]
    herd: [spy-cara, spy-bex, spy-ovo]
```

Validation rules (in `CompetitionConfig` + the existing
`WorldConfig._check_cast_references` validator):

- `teams` is permitted only when `kind: versus`, must be non-empty there, with
  non-empty, mutually disjoint member lists.
- Every team member must be in the scenario's `cast`.
- **No team label may collide with an agent name** — `winner` carries either an agent
  name or a team label, and this rule is what keeps that union unambiguous.

The Steeped declares `versus` with the teams above; Mystery Roots declares `judged`;
Thousand Token Wood declares nothing (`none`). `kind: none` scenarios keep full
sessions/history (ADR-0027) — they simply never produce a winner.

### 2. Well-known typed extra fields (extends ADR-0016)

`output_extra_fields` stays a `list[str]` — no manifest syntax change. Instead,
`src/core/structured.py` gains a small table of **well-known field types**:

| field    | type                | required |
|----------|---------------------|----------|
| `winner` | `str \| None`       | no (default `None`) |
| `scores` | `dict[str, float]`  | no (default `{}`)   |
| *other*  | `str`               | yes (unchanged)     |

`build_output_model` consults the table; `json_instruction` renders a typed schema
hint for known fields (`"winner": "<name or null>"`, `"scores": {"<name>": 0-10}`).
`winner` and `scores` are not arbitrary scenario fields — they are the verdict
contract ADR-0026 already names in `run.finished` — so giving them engine-known types
is the same move as `CORE_EVENT_KINDS`: open surface, curated core. Back-compat is
total: every existing manifest (`[mood]`, `[thought]`, …) hits the *other* row and
behaves exactly as before; the offline parser needs no change because it already
passes non-string values through.

### 3. Winner validation and one re-ask, in the base agent

Validation lives in `ManifestAgent`, not in per-judge handlers — `_resolve_payload`
owns the model call, so it is the only seam where a re-ask is one extra round-trip
instead of a re-architecture:

- The registry attaches the scenario's competition context when assembling a cast:
  `agent.competition = cfg.competition` in `build_scenario`, plus the cast name list
  and team labels (the *valid winner vocabulary*).
- A new overridable hook `_validate_payload(parsed) -> str | None` runs after the
  structured call (and after the offline parse). The base implementation activates
  only when `role == "judge"`, a competition with `kind != none` is attached, and
  `winner` is in the agent's extra fields. A present-but-unknown `winner` (not a cast
  name, not a team label) returns an error string; a missing/`None` winner is *not*
  an error (the field is optional, and the offline stub never emits it — determinism
  preserved).
- On error, `_resolve_payload` re-asks **once**: the same prompt plus a corrective
  line naming the valid options. Token usage from both calls is *accumulated* into
  `last_usage` so the governor (ADR-0013) meters the retry. On a second failure the
  invalid `winner` is dropped and `payload["no_contest"] = true` is stamped — the
  verdict text still ships (the drama survives), the leaderboard simply gets no row.
- `scores` is validated but never re-asked (it is garnish, not load-bearing): unknown
  agent keys are dropped, values clamped to 0–10.

### 4. Ground truth belongs in code (the versus path)

For `kind: versus`, the LLM's `winner` field is its **accusation** (a cast name,
validated by §3). The scenario's handler — `SpyHost` is the template — then computes
the scoreboard after `super().act()`:

```
accused  = payload.pop("winner")            # the judge's pick, kept as payload["accused"]
correct  = accused in competition.teams["spy"]
winner   = "herd" if correct else "spy"     # team label, stamped by code
payload  |= {"accused": accused, "correct": correct, "winner": winner}
```

The team map comes from `competition.teams`, not from a handler constant — the
curated `_REVEAL` dict stays what it is (demo content: secrets and reveal drama),
while *who is on which team* is scenario config. Offline fallback: when the stub's
verdict carries no `winner`, the handler scans the verdict text for the first cast
name mentioned and treats it as the accusation; if none is found, `no_contest`. This
keeps the no-API-key demo producing a full stamped scoreboard, deterministically.

For `kind: judged` (Mystery Roots), no handler is needed: the validated `winner`
*is* the result — AI is load-bearing for the judgment itself. For `kind: none`,
judges do not declare `winner` in their manifests at all; `mischief-critic` keeps
`[mood]` (the Wood's reckoning records what became real — nobody wins it).

### 5. Attribution contract (extends ADR-0026)

`winner` is now an agent name (*judged*) or a team label (*versus*), so the
`run.finished` payload and `RunSummary` gain two additive keys:

```
"winner":         str | None    # unchanged — display name for the leaderboard row
"winner_kind":    "agent" | "team" | None
"winning_models": list[str]     # model_endpoint of the winner, or of every
                                # member of the winning team (None entries dropped)
```

`winning_model` keeps its exact current meaning — a single cast agent's model — and
is populated only when `winner_kind == "agent"`; it is `None` for team wins (never a
guess). `FishbowlSession.finalize` resolves the kind by checking the winner against
the run's cast map first, then the scenario's team labels. A future leaderboard
renders one row per finished run in a `kind != none` scenario: winner name,
`correct` badge where present, and the winning model(s). The UI itself is out of
scope here.

## Consequences

- **The verdict is data and drama at once.** `judge.verdict` carries
  `winner`/`accused`/`correct`/`scores` machine-readably while `text` stays the
  spoken ruling; `finalize`'s existing best-effort read starts actually working.
- **All additive.** No schema bump, no migration; old ledgers, old manifests, and the
  string-typed extra-field behaviour are untouched. The offline stub emits no
  `winner`, so the deterministic demo is byte-identical except where the SpyHost
  text-scan stamps the scoreboard.
- **One re-ask is bounded cost.** At most one extra model call per verdict, metered
  by the governor; the failure mode is a missing leaderboard row, never a missing
  show ending.
- **The well-known field table is a curated list in engine code.** A scenario cannot
  invent a new *typed* field from YAML alone — accepted: arbitrary fields remain
  available as strings, and a handler can always derive structure from them. If a
  third typed field ever appears, revisit a declarative field-spec syntax (see
  alternatives).
- **Validation vocabulary is injected, not discovered.** Agents now carry a small
  piece of scenario context (`competition`). This is a deliberate, single-attribute
  seam mirroring `agent.manifest`; it does not give agents the scenario object.
- **Risk: usage accounting on re-ask.** `last_usage` must sum both calls or the
  governor undercounts; this is an explicit acceptance criterion, not an afterthought.
- Prize impact: strengthens Best Agent / Best Demo (code-stamped scoreboard over
  small-model judgment) and Sharing-is-Caring (self-scoring trace); the future
  leaderboard it enables feeds Community Choice polish. No track is disqualified;
  the ≤32B constraint is untouched.

## Alternatives considered

- **Typed field descriptors in manifest YAML** (`output_extra_fields: [{name: scores,
  type: score_map}]`). Maximally declarative, but adds a config surface and a union
  schema for exactly two fields the engine already treats as core in ADR-0026.
  Rejected as not the thinnest slice; the well-known table can grow into this later
  without breaking `list[str]`.
- **Keep extra fields string-typed; handlers parse `winner`/`scores` out of strings.**
  No engine change, but every judge handler re-implements parsing and the live path
  loses validation-by-construction — the exact regression ADR-0016 exists to prevent.
- **Validate/re-ask in the conductor or per-handler.** The conductor never re-prompts
  (it has no prompt), and per-handler re-ask duplicates the retry across
  spy-host/mystery-judge and inverts the `super().act()` flow. The base-class hook is
  the only seam that owns both the prompt and the provider.
- **Let the LLM declare the team winner in versus scenarios.** Simpler wiring, but
  the model can be *wrong about its own conclusion's consequence* (naming the spy yet
  declaring the spy won). Where truth exists, code stamps it — this is the
  load-bearing split the feature exists to demonstrate.
- **A separate `judge.scored` event kind.** Keeps `judge.verdict` lean, but splits
  one ruling across two events that every consumer must re-join, and the Fishbowl
  curtain-fall already keys on the first `judge.verdict`. One enriched event wins.
- **Reuse `winner` for the accusation and skip `accused`.** Loses the LLM's actual
  pick once the handler overwrites it; `accused` + `correct` is what makes the trace
  auditable.

## References

- ADR-0009 (open/additive event kinds — new payload keys, no schema bump)
- ADR-0011 (declarative validatable config — `CompetitionConfig` joins `ScenarioConfig`)
- ADR-0016 (validated structured output — `build_output_model` typing extended)
- ADR-0026 (run lifecycle — `run.finished` winner contract extended with
  `winner_kind` / `winning_models`)
- `src/core/structured.py` — well-known field types, typed `json_instruction`
- `src/core/config.py` — `CompetitionConfig`, cross-validation in `WorldConfig`
- `src/agents/base.py` — `_validate_payload` hook, single re-ask, usage accumulation
- `src/agents/handlers.py` — `SpyHost` ground-truth scoreboard
- `src/core/registry.py` — competition context injection in `build_scenario`
- `src/ui/fishbowl/session.py`, `src/core/run_index.py` — attribution resolution
