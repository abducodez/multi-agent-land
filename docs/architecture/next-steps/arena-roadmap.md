# Arena Roadmap: sessions, winners, leaderboards, and a louder commentator

> Status: proposed (senior-architect review, June 2026). Companion to
> [architecture-review-and-next-steps.md](architecture-review-and-next-steps.md).
> Goal: evolve the forest theater into a **multi-agent arena** — many agents play
> scenarios as *games*, a judge picks a winner, every run is preserved as a session,
> and a leaderboard tracks which agents *and which models* win the most.

Everything below follows the house rule: **the ledger is the only source of truth;
every new feature is a typed event plus a projection.** No side databases that can
drift from the log.

---

## Where we are today (grounded in the current tree)

| Capability | State | Evidence |
|---|---|---|
| Event ledger | ✅ 3 backends (memory / SQLite / SQLAlchemy-Postgres) | `src/core/ledger.py`, `src/core/sqlite_ledger.py`, `src/core/sqlalchemy_ledger.py` |
| Run identity | ◐ `run_id` on every event, indexed — but **nothing queries by it** | `src/core/events.py:51`, `src/core/sqlite_ledger.py:60` |
| Multi-run history | ❌ projections/memory assume one ledger = one run; UI session is ephemeral `gr.State` | `src/core/projections.py`, `src/core/memory.py`, `src/ui/fishbowl/session.py` |
| Run end | ◐ implicit — autoplay halts on first `judge.verdict`, budget trip, or tick backstop; no `run.finished` event | `src/ui/fishbowl/session.py:98-136` |
| Winner | ❌ verdict is prose (`"Verdict: …"`); no structured `winner` field; no ground-truth check | `config/agents/spy-host.yaml`, `src/agents/handlers.py` |
| Competitive scenarios | ◐ only The Steeped is a real game; Open Table and Oracle Grove have **no judge at all** | `config/scenarios/*.yaml` |
| Leaderboard / history UI | ❌ none | `src/ui/fishbowl/` |
| Commentator | ❌ none (narrator feed is a transcript, not commentary) | `src/ui/fishbowl/show.py` |
| Models | ◐ Modal vLLM (Nemotron 4B/30B/14B, MiniCPM 8B + o-4.5, Gemma 4 12B/26B), HF serverless (Arch-Router-1.5B), in-process Local GPU (Qwen2.5-3B/7B, MiniCPM4.1-8B — ADR-0033), stub. No fine-tune, no gpt-oss | `modal/catalogue.py:144-296`, `src/models/hf_catalogue.py:71-85`, `src/models/local_catalogue.py` |

---

## Workstream 1 — Sessions & run history (the foundation; do this first)

Everything else (leaderboard, replays, trace export) depends on runs being
first-class and queryable. Four steps:

### 1.1 New event kinds: `run.finished` (and enrich `run.started`)

- Add `run.finished` to the core kinds in `src/core/events.py` with payload
  `{reason: "verdict" | "budget" | "tick_cap" | "user_stop", winner: str | null,
  winning_model: str | null, turns: int, tokens: int}`.
- Enrich the `run.started` payload (emitted in `Conductor.reset()`,
  `src/core/conductor.py:102`) with the **full cast → model binding map**
  (`{agent_name: {model_endpoint, model_profile}}`) and the scenario name. The
  Lab already knows the per-agent bindings (`world.agents[i].model_endpoint`,
  ADR-0022) — stamp them into the event so a run is self-describing forever.
  Without this, "which model won" is unanswerable later.
- Emit `run.finished` from the place that already decides the show is over:
  `FishbowlSession`'s halt logic — plus `Conductor` when the Governor trips, so
  headless runs also close their runs. Bump `schema_version` if payload shapes
  change; write a short ADR (this is a new public event kind, per ADR-0009).

### 1.2 Make projections run-scoped

- Every projection/memory constructor that reads the ledger takes a `run_id`
  and filters on it: `StageProjection.rebuild_stage()`
  (`src/core/projections.py:46`), `EpisodicMemory` / `SalienceMemory`
  (`src/core/memory.py`), and `has_verdict()`
  (`src/ui/fishbowl/session.py:98`). Default to "the conductor's current
  run_id" so existing call sites keep working.
- Add `events_for_run(run_id)` / `runs()` query methods to the ledger protocol
  and all three backends (SQLite already has the index; the in-memory ledger
  filters a list). This is the single API the history browser, replay, export,
  and leaderboard all build on.

### 1.3 Stop treating the ledger as disposable

- `Conductor.reset()` must **never clear a persistent ledger** — it only mints
  a new `run_id` and appends a fresh `run.started`. One DB, many runs.
- `FishbowlSession`: on "Summon", reuse the shared persistent ledger
  (`make_ledger()`, `src/core/ledger_factory.py`) instead of a per-session
  in-memory one when `DATABASE_URL` is set; fall back to in-memory for the
  no-key stub demo. Multiple browser sessions then naturally share history
  while each driving their own `run_id`.

### 1.4 `RunIndex` projection + trace export

- `src/core/run_index.py`: a pure function `index_runs(events) -> list[RunSummary]`
  (Pydantic v2 model: run_id, scenario, seed, cast+models, started/finished,
  reason, winner). For SQL backends add an equivalent single-query
  implementation; keep the pure-Python one as the reference/test oracle.
- `scripts/export_trace.py <run_id>`: dump one run as JSONL + a README and push
  to the HF Hub (📡 *Sharing is Caring* badge — the ledger **is** the trace,
  this is nearly free money).

**Deliverable check:** after W1, "support multiple sessions and keep the history
of runs" is done at the data layer; the UI work is W5/W6.

---

## Workstream 2 — Structured verdicts: winners as data, not prose

### 2.1 Structured `judge.verdict` payload

Judges already use structured output (instructor, ADR-0016) and
`output_extra_fields`. Extend judge manifests with `winner` (an agent name from
the cast, or a team label) and optional `scores: {agent: 0-10}`:

```yaml
# config/agents/spy-host.yaml (and mystery-judge, mischief-critic)
output_extra_fields: [mood, winner, scores]
```

The verdict event then carries `payload.winner` machine-readably. Validate the
name against the cast in the agent's handler; on mismatch, re-ask once, then
fall back to "no contest".

### 2.2 Ground truth belongs in code, not the model

Where a scenario *has* a ground truth, the **handler** decides the winner, not
the LLM's prose. The Steeped is the template: `SpyHost`
(`src/agents/handlers.py:18-58`) already attaches the reveal map — extend it to
compare the judge's named spy against the actual spy and stamp
`payload.winner = "spy" | "herd"` plus `payload.correct: bool`. The LLM
provides the drama; the handler provides the scoreboard. This is the
best-practice split: AI is load-bearing for judgment, code is load-bearing for
bookkeeping.

### 2.3 Scenario competition contract

Add an optional block to scenario YAML (validated in `WorldConfig`,
ADR-0011):

```yaml
competition:
  kind: versus | judged | none   # The Steeped: versus; Mystery Roots: judged; Wood: none
  teams:                          # optional, for team games
    spy: [spy-nil]
    herd: [spy-cara, spy-bex, spy-ovo]
```

The leaderboard uses this to know which scenarios produce winners and how to
attribute them. `kind: none` scenarios still get sessions/history, just no
leaderboard rows.

---

## Workstream 3 — Make every scenario arena-grade

Audit of the five current scenarios and what each needs:

| Scenario | Today | To be arena-grade |
|---|---|---|
| 🕵 The Steeped | real game, prose verdict | W2.2 handler winner + team attribution — the flagship |
| 🔍 Mystery Roots | judge declares an explanation | `competition: judged`; winner = the agent whose hypothesis the judge endorses (judge names it via `winner` field) |
| 🍄 Thousand Token Wood | collaborative, mischief-critic "reckoning" | keep `kind: none`; optionally let the critic score each agent's contribution (fun stats, no winner) |
| 💬 Open Table | **no judge** — runs until budget | add a lightweight `table-judge` (balanced tier) that, after N rounds, names the most persuasive voice; `competition: judged` |
| 🔮 Oracle Grove | **no judge**, showcase only | either add a "did the prophecy land" judge or explicitly mark `kind: none` showcase |

New competitive scenarios — each is **pure YAML + maybe one small handler**, and
each makes the model-leaderboard more meaningful because different models can
sit in symmetric seats:

1. **⚔️ Debate Duel** — two debaters (identical manifests, different models!),
   fixed rounds, judge picks the winner. The cleanest "which model argues
   better" arena; near-zero new code.
2. **❓ Twenty Sprouts** (20-questions) — a `secret-keeper` holds a word (dealt
   by a handler, like the spy words), a `guesser` asks yes/no questions; handler
   checks the final guess. Deterministic win condition, very watchable.
3. **🎭 Beat Battle** — two storytellers alternate story beats on the same seed;
   the judge crowns the more delightful one. Reuses the Wood's machinery,
   directly demos the "delight" judging criterion.

Add a **scenario authoring checklist** to
`docs/architecture/scenario-authoring.md`: premise + cast + governor +
`competition` block + end condition + structured verdict + reveal text. "Make
sure all game scenarios are perfect" = every scenario passes this checklist,
enforced by a small test that loads each YAML and asserts the contract.

---

## Workstream 4 — Models: more ≤32B options, more prize lanes

The router/catalogue design means each item here is a catalogue entry, not an
engine change:

1. **gpt-oss-20B on Modal** — CLAUDE.md calls OpenAI the home track, yet no
   OpenAI open model is in the catalogue. Add `gpt-oss-20b` (vLLM serves it) and
   make it the `strong`-tier default for the live demo path. Highest strategic
   priority in this workstream.
2. **Local GPU backend** (✅ *shipped* — ADR-0033) — third backend in
   `src/models/inference.py`'s registry: in-process `transformers` via
   `@spaces.GPU` (`local:` key prefix, `LOCAL_INFERENCE=1` opt-in). Works on
   ZeroGPU, dedicated-GPU Spaces, and local CUDA boxes. Supports the Community-
   Choice / Tiny-Titan / OpenBMB lanes. **Note:** this replaces the earlier
   llama.cpp design (ADR-0032) — the 🦙 Llama Champion runtime badge is not
   pursued; on-device inference ships instead as the in-process GPU path.
3. **Broaden the Modal catalogue** with strong ≤32B chat models so arena seats
   differ meaningfully: Qwen3 (4B/8B/14B/32B), Llama-3.1-8B-Instruct,
   Phi-4-14B, Mistral-Small-24B. Prefer models vLLM serves without
   `trust_remote_code` gymnastics. (Note from ops memory: HF *serverless*
   provider enablement is flaky on this account — keep Modal the primary live
   path.)
4. **Fine-tuned specialist** (🎯 *Well-Tuned*, highest effort) — export judge
   or commentator transcripts from the ledger (W1.4 gives you the data), LoRA-tune
   a 4B (Qwen3-4B or MiniCPM) into a *house commentator/judge voice*, publish on
   HF, route via an `hf:` or Modal key. The fine-tune data coming from our own
   ledger is a great Field Notes story.
5. **Per-seat model assignment in the Lab is already built (ADR-0022)** — the
   arena framing just needs W1.1's binding stamp so wins attribute to models.

---

## Workstream 5 — Spectator experience: the commentator booth

### 5.1 Commentator agent (config-only, thanks to subscription routing)

```yaml
# config/agents/booth-commentator.yaml
name: booth-commentator
role: worker
persona: >
  You are the excitable color commentator of the forest arena. React to the last
  thing said in ONE punchy sentence — playful, partisan, never neutral. Also pick
  a `reaction` that matches: one of [gasp, laugh, suspicious, mindblown, facepalm,
  popcorn, clap, drama].
subscribes_to: [agent.spoke, judge.verdict]
may_emit: [commentary.note]
schedule: {}                    # purely reactive — fires only on subscription
model_profile: tiny             # ≤4B doing real work → 🐜 Tiny Titan exhibit
output_extra_fields: [reaction]
```

- New open event kind `commentary.note` (ADR-0009 — no engine change needed).
- **Keep it off the critical path:** commentary must never block or end the
  show. Exclude `commentary.note` from `has_verdict()`/halt logic and from other
  agents' visible memory kinds (the cast shouldn't hear the booth — it would
  pollute the game). Both are one-line filters.
- Throttle: subscribe-on-`agent.spoke` fires every utterance; add a
  `subscription.cooldown_turns: 1` manifest field (small Conductor change in the
  subscription queue, `src/core/conductor.py:273-278`) so the booth comments at
  most once per round.

### 5.2 Reactions panel with GIF energy — local-first

Map the structured `reaction` field to a **curated local set of looping
animations** (animated SVG/WebP in `src/ui/fishbowl/assets/reactions/`), not a
live GIPHY/Tenor call:

- Reliable on stage and in the no-API-key stub demo (a hard CLAUDE.md
  constraint); no network jitter mid-demo, no licensing surprises.
- 8 reactions × 2-3 variants each is plenty; the *commentary text* (the AI part)
  carries the variety — which keeps AI load-bearing, with assets as garnish.
- UI: a "📺 Commentary Booth" panel in the Show's right sidebar
  (`src/ui/fishbowl/show.py`) above the narrator feed: reaction animation +
  rolling last-3 comments, updated on the same `gr.Timer` tick that drives the
  show. Optionally a `REACTIONS_GIPHY_KEY` env flag for live GIF search later —
  strictly additive.

### 5.3 Verdict ceremony

When `run.finished` lands: confetti/spotlight animation on the winner's
MindCard, the reveal map rendered as a "case file", and the booth's final hot
take. This is the "would you show a friend" moment — budget real polish time
here.

---

## Workstream 6 — Hall of Fame (leaderboard)

Pure projection over the ledger — no new storage, fits ADR-0001 exactly.
Requires W1 (run lifecycle) + W2 (structured winner).

### 6.1 `src/core/leaderboard.py` — pure functions

```python
def scenario_sessions(events, scenario) -> list[SessionRow]   # run, date, cast, winner, reason
def model_table(events) -> list[ModelRow]                     # model, plays, wins, win_rate
def agent_table(events, scenario) -> list[AgentRow]           # per-persona wins within a scenario
```

Joins `run.started` (cast→model map) with `run.finished` (winner). For SQL
backends, an aggregate query version; the pure version stays the test oracle.
Only runs whose scenario declares `competition.kind != none` produce rows.

### 6.2 "🏆 Hall of Fame" tab in the Fishbowl

- Scenario picker → sessions table (every past run: who played, which models,
  who won, why it ended) with a **Replay** button per row.
- Model leaderboard: win counts + win rate per model across all competitive
  scenarios — this is the headline "MiniCPM-8B has beaten Gemma-12B 7–3 at
  Debate Duel" artifact, and it's a *killer demo line* for the sponsor tracks.
- **Replay**: load `events_for_run(run_id)` into the Show tab's existing
  scrub/replay transport (it already replays events — feed it historical ones).
  History browsing falls out of the same mechanism.

### 6.3 Fairness footnote (do early, it's cheap)

Win rates mislead if seats are asymmetric (the judge's model never "wins"; the
spy seat is harder than herd seats). Record the seat in the attribution row and
display win rate *per seat type*. For Debate Duel, alternate which model goes
first across sessions.

---

## Sequencing & dependencies

```
Phase A (foundation)  W1 sessions/run-lifecycle  +  W2 structured winners     ← everything depends on this
Phase B (arena)       W3 scenario audit + Debate Duel  +  W6 Hall of Fame
Phase C (show)        W5 commentator booth + reactions + verdict ceremony
Phase D (reach)       W4 models (gpt-oss → llama.cpp → catalogue → fine-tune)
```

- A → B is a hard dependency; C and D are independent of B and can interleave.
- Each phase lands with its ADR + doc updates (ADR-0004), additive tests
  (zero mocks — the deterministic stub makes leaderboard/session tests easy:
  stub runs are reproducible, so winner attribution is assertable), and the
  no-API-key stub path kept fully working.
- Prize coverage per phase: A unlocks 📡 trace export; B unlocks the Best
  Agent/Best Demo arena story; C is the delight criterion + 🐜 Tiny Titan; D is
  OpenAI track + 🎯 (Local GPU / on-device inference already ships for Community-
  Choice, OpenBMB, and Tiny-Titan lanes — 🦙 Llama Champion is not pursued).

## Beyond (post-arena ideas, in rough value order)

- **Tournament mode**: a script that schedules N sessions across model
  pairings (round-robin Debate Duel), filling the Hall of Fame automatically —
  great for the demo video and for publishing a "small-model arena" dataset.
- **Audience play**: `user.injected` already exists — let spectators vote on
  the verdict before the judge rules, and show "audience vs judge" agreement.
- **Elo instead of raw wins** once session counts grow.
- **Booth duo**: two commentators (different models) with opposing biases —
  doubles the sponsor-model surface and the comedy.
