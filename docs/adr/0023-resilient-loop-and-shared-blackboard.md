# ADR-0023: Resilient Turn Loop, Shared Blackboard, and a Leak-Safe Parser

## Status

Accepted

## Context

The first live run of a *conversation* scenario (`the-steeped`, the word-pair bluff
game) surfaced three failures that the offline stub hid. Only one player ever spoke,
repeating the same line every turn, and that line leaked the secret word:

```
t1 spy-cara: A dark, steaming brew that warms the soul…
t2 spy-cara: We need to output JSON… Thought: I think the word is COFFEE. Mood: smug…
t3 spy-cara: A dark, steaming brew…  (and so on, only ever cara)
```

Tracing it through the engine, four distinct root causes:

1. **The shared blackboard wasn't shared.** `StageProjection.current_scene` is only
   ever set by `run.started`/`world.observed`; peers' `agent.spoke` lines land in
   `agent_notes`, which `ContextBuilder` never injected — and `agent.spoke` is not in
   memory's `_GLOBALLY_VISIBLE`. So every agent acted in a vacuum: it saw only its own
   persona, the world text, and its own past lines. A small model with that prompt has
   nothing new to react to, so it loops. (This was the headline finding of the earlier
   architecture review.)

2. **One agent's failure aborted the whole tick.** `_run_agent` ran with no isolation.
   Live, the salience-using agents (`spy-bex`, `spy-host`) read through the mem0
   `MemoryIndex`; when that backend threw, the exception propagated out of the tick, so
   every later agent went silent and the next tick simply re-ran the first agent. That
   is the exact "only cara, looping" signature.

3. **The public setup published the secret.** `default_seed`/`genesis_text` literally
   named "Three share COFFEE; one lone spy holds TEA", which the conductor writes into
   `run.started` (the seed) and `world.observed` (the scene) — both globally visible. So
   every mind, and the audience, were handed the answer.

4. **Chain-of-thought reached the ledger as the spoken line.** When the live structured
   call (`complete_structured`/Instructor) failed on a reasoning model's preamble, the
   fallback `parse_agent_output` wrapped the *raw* text — the scratchpad, which names the
   word — as `text` (`_raw_fallback`). The old `\{[^{}]+\}` extraction also broke on
   nested braces.

## Decision

Fix all four at the layer that owns each concern; enrich the core where the contract was
missing, keep every change additive and offline-reproducible.

1. **Share the blackboard.** `ContextBuilder` now injects a `WHAT'S BEEN SAID` block from
   `projection.agent_notes` (the public `text` of recent peer events — never their private
   `thought`), with a "react / add a NEW line / never repeat" nudge. This is the single
   highest-value fix: agents now reason over the live table. Within a tick the projection
   is mutated as each agent acts, so later speakers in the same round see earlier ones.

2. **Make the turn loop resilient.** `_run_agent` isolates a failing `act()`: it records the
   failure on `Conductor.agent_errors` and continues the tick, so one crash never silences
   the cast. `BudgetExceeded` is re-raised — the governor's intentional stop is never
   swallowed. Defensively, `SalienceMemory` now treats the `MemoryIndex` as the
   derived, rebuildable lens ADR-0018 always intended: any index error degrades to keyword
   relevance instead of throwing.

3. **Keep the secret private.** The public seed/genesis for `the-steeped` no longer name the
   words or who holds what. Each mind learns *only its own* word from its persona; the
   `spy-host` reveal map unmasks the rest at the verdict. Memory formatting also stopped
   dumping `str(payload)` (which leaked the raw seed dict) — it renders `text`/`summary`, or
   `run.started`'s shared `goal`, and nothing else.

4. **Harden the parser ("inductor").** `parse_agent_output` strips tagged reasoning blocks
   (`<think>…`) and code fences, then parses the **last** balanced `{…}` object via a
   string-aware brace scan (a reasoning model puts its answer last). When no object parses,
   it *salvages* a safe line — the quoted `"text"`/`Text:` value the model intended — and
   falls back to a neutral placeholder rather than ever shipping the scratchpad. The JSON
   instruction now says, explicitly, output-only-JSON / no-analysis / never-spell-a-secret.

## Consequences

- **Conversation scenarios work.** The whole cast speaks each round, reacts to peers, and
  stops looping; a single flaky agent degrades to a skipped turn, not a dead show.
- **The bluff is a real bluff.** The word is no longer in the opening narration, the memory,
  or (via salvage) the spoken line — guarded by tests in `test_spy_game.py`.
- **Cross-scenario benefit.** The shared blackboard and the resilient loop help every cast,
  not just the spy game — Mystery Roots and the Wood now see each other's lines too.
- **Offline reproducibility holds.** The instruction keeps the `Schema:` / `kind must be one
  of:` markers the deterministic stub parses; all 400+ tests stay green, zero mocks.
- The blackboard is injected unconditionally. If a future scenario needs strict per-agent
  information hiding for *spoken* events, that would move to a manifest-level visibility
  grant (noted, not built — current scenarios want the shared table).

## Follow-up: reasoning models, token budget, and the mind-reader thought

The first live run after the fixes above exposed a second-order failure: the cast went
silent again — every event `_raw_fallback`, most just `…`. The cause was the *model
tier*, not the loop:

- `balanced` (gemma-4-12B-it) and `strong` (gemma-4-26B-A4B-it) are served **with a
  reasoning parser** (`modal/catalogue.py`: `reasoning_parser="gemma4"`). They *think*
  before answering, and that thinking counts against `max_tokens`. With `balanced` capped
  at **320 tokens**, the model was truncated mid-thought and emitted an **empty** answer →
  the `…` placeholder. Three of the four players were pinned to that one tier.
- The model's actual thinking lands in a separate `reasoning_content` channel that we
  discarded — which is exactly the "thinking" we want to surface (mind-reader) and keep
  out of other agents' prompts.

Decisions:

1. **Budget for the thinking.** Raise `max_tokens` on the reasoning tiers (`balanced`
   320→768, `strong` 480→1024; `fast` 220→320) in `config/models.yaml` and the router
   defaults, so the model finishes thinking *and* emits the answer. These are tunable —
   higher = fewer truncations but slower turns.
2. **Capture reasoning as the mind-reader thought.** `LiteLLMProvider.last_reasoning`
   reads `message.reasoning_content` (vLLM reasoning parsers). When an agent wants a
   `thought` and the model gave none (the fallback path), `ManifestAgent._with_reasoning`
   fills it from that reasoning (or inline `<think>` tags). It rides only on that event's
   payload — the blackboard and memory share `text` alone, so **a peer never reads another
   mind's thinking**. The UI shows it only under the "read their minds" toggle.
3. **Diversify the cast.** Move `spy-bex`→`fast` (MiniCPM) and `spy-ovo`→`tiny` (Nemotron)
   so it isn't "all the same model," a single model can't sink three agents, and two
   players sit on non-reasoning models that can't truncate. Also showcases the
   per-agent multi-model design (the project's prize thesis).
4. **Salvage a real clue.** `_salvage_text` now recovers the model's intended line from a
   closed `"text"` value, a trailing unterminated draft-quote, or scratchpad-dropped
   sentences — only `…` when nothing survives.

## Follow-up: the live fallback fights weak models, and the host must commit

A third live run showed two more issues:

- **Re-prompting with the JSON schema backfires on weak models.** When the structured
  call failed, the old fallback appended the *same* `json_instruction` and ran the
  tolerant parser. Small / reasoning models then **echoed the instruction** ("Need to
  output JSON with kind agent.spoke…"), **copied the example** verbatim ("A brief,
  evocative response."), and **leaked the secret while reasoning** ("…Secret word is
  COFFEE…") straight into the spoken line.
- **The show stopped on a non-verdict.** The host (`tick_every: 3`) fired and *stalled*
  ("let the final clues be cast before I dissect") — yet the Fishbowl autoplay halts on
  the first `judge.verdict`, so the show ended on a non-accusation.

Decisions:

1. **Plain-prose live fallback (no JSON to echo).** When `complete_structured` fails (or
   returns an empty line), `ManifestAgent._prose_fallback` re-prompts for *one or two
   in-character sentences and nothing else* — no schema, no example, no fields. The
   answer is cleaned by `structured.clean_clue`, which strips reasoning blocks and drops
   meta/instruction/secret-word sentences (`_META`), returning the clue plus the residue
   (used as the private thought). The offline stub path is unchanged (it parses the JSON
   instruction fine).
2. **Skip a turn rather than ship junk.** If the cleaned clue is degenerate (empty, `…`,
   the example echo, or all-meta — `is_usable_line`), the agent raises `AgentOutputError`;
   the resilient loop records it on `agent_errors` and moves on. No `…` or scratchpad
   ever reaches the stage; the blackboard lets the rest of the cast carry the round.
3. **The host commits.** `spy-host`'s persona now requires it to name exactly one mind as
   the spy in its single message — no deferring, no "before I dissect". It fires once
   after four full rounds (`tick_every: 4`), and that verdict ending the show is by
   design (`has_verdict()` halts autoplay).

## Follow-up: stream one agent at a time

`step()` runs a whole turn (every scheduled agent) before returning, so the Show only
updated once the last mind had spoken — a ~40s blank wait per turn on the live path.
`Conductor.step_one()` is the streaming counterpart: it drains a per-turn ``_pending``
queue one actor at a time (opening a new turn — incrementing ``turn``, checking the
governor, queuing this turn's subscription + tick actors — only when the queue empties,
and absorbing triggered subscribers into the same turn so cascades still resolve). The
Fishbowl autoplay and the ⏭ button now call `step_one`, so each agent appears the moment
it responds. The loop-safety backstops are unchanged (the counter still increments per
generating advance; the head grows by at most one). `step()` keeps the
turn-at-a-time semantics for tests, cron, and resume.

## Follow-up: truncated reasoning, model-error sentinels, and no echoing

A later live run surfaced three more leaks/quality issues, each fixed at its layer:

- **Unterminated reasoning.** A reasoning model truncated mid-think emits
  `<think>Alright, the user wants me to play as CARA… Since COFFEE is common, I shou` with
  no closing tag — the old stripper only removed *closed* `<think>…</think>`. `_strip_reasoning`
  now also drops everything from an unterminated open tag to the end, and `extract_reasoning`
  captures that tail as the (private) thought. So the truncated monologue — and the secret it
  names — never becomes the line; the turn is skipped.
- **Generic secret-word guard.** Beyond `<think>`, a model sometimes names the word in bare
  prose ("Since COFFEE is common…"). `clean_clue` now drops any sentence containing a
  standalone ALL-CAPS token (`_CAPS_TOKEN`, ≥3 letters) — personas write secrets as
  COFFEE / TEA / TREE and a slip echoes them in caps, where a clue never would — plus broader
  reasoning-preamble patterns (Alright / the user / Looking at / play as / I should…).
- **Model-error sentinels.** `complete()` returns `"[model error: …]"` (it can't raise — it
  returns `str`) on a transient connection drop. `is_model_error` / `_guard_model_error` turn
  that back into an `AgentOutputError` so the resilient loop skips the turn instead of speaking
  the raw connection error as a clue or verdict.
- **No echoing (conversation flow).** Small models ignore "never repeat" and looped a single
  line verbatim across the whole cast. `ManifestAgent._is_repeat` skips a spoken line that
  near-duplicates (token-set Jaccard ≥ 0.8) a recent one on the ledger, and the blackboard nudge
  now demands a genuinely new angle. **Live only** — the offline stub's curated catalogue is
  reproducible by design, and de-duplicating its small line set would starve demos and tests.

## Follow-up: recall the whole discussion, and let judges rule on all of it

The original fix shared peers' lines *within a round* (the `WHAT'S BEEN SAID` blackboard,
the last ~6 of `projection.agent_notes`). A later audit — "are we passing good context
about the discussion for **every** scenario?" — found two gaps that the blackboard tail
hid:

- **Memory never carried the discussion.** `agent.spoke` was not in `_GLOBALLY_VISIBLE`,
  so an agent's recall (episodic *and* salience) contained only its own lines plus world
  beats/verdicts — never a peer's spoken line. The blackboard's recent tail was the
  *only* window onto the conversation.
- **Judges ruled on a 6-line tail.** A judge fires late and has no own-events yet, so its
  salience candidates were just the globally-visible kinds — i.e. **zero** of the
  discussion. Measured across the shipped casts, every judge recalled `0` spoken lines and
  saw only the last 6 via the blackboard: `open-table` 6 of 18, `the-steeped` 6 of 16
  (missing half the clues that locate the seam), and so on.

Three changes, each at the layer that owns the concern:

1. **Public speech is recallable; private thought is not.** `_GLOBALLY_VISIBLE` gains
   `agent.spoke` and `oracle.spoke` (`src/core/memory.py`). A spoken line is the shared
   table — every mind can recall it across the whole run, not just this round. `agent.thought`
   stays out (the mind-reader's alone), secrets ride non-`text` payload keys, and
   `_displayable` shows `text` only — so sharing speech leaks nothing (verified: the spy
   word and the sprout word never appear in a peer's prompt). Reflection cadence is kept on
   a separate, narrower `_REFLECTION_VISIBLE` set so a chatty table doesn't change how often
   an agent compacts memory.

2. **The discussion block is role-aware** (`ContextBuilder`, ADR-0006's "builder owns
   assembly"). Workers still get `WHAT'S BEEN SAID` — the recent table to react to.
   **Judges get `THE EXCHANGE TO JUDGE` — the complete, ordered public transcript**, so a
   ruling weighs the whole debate, not its tail. `YOUR MEMORY` is deduped against whichever
   block is shown (it holds the *earlier* arc + world/verdict beats; a line is never
   printed twice), and shows a short pointer when the transcript already covers it. After
   the change every judge sees 100% of the discussion (`18/18`, `16/16`, …).

3. **A mis-wired scenario, and a cascade guardrail.** Mystery Roots' `clue-gatherer` and
   `devils-advocate` emitted private `agent.thought` — so the hypothesis-former reasoning
   "based on the clues gathered" saw none, and the judge saw nothing. Their personas are
   plainly public contributions, so they now `agent.spoke` (keeping a private `thought` for
   the mind-reader). That exposed a latent footgun: an agent that both subscribes to and
   emits `agent.spoke` re-triggered itself, cascading until the per-turn call cap tripped
   *before the judge fired*. `Conductor._notify_subscribers` now never queues an agent for
   its **own** event — self-reaction is never intended, and a subscriber still reacts to
   every peer's event.

Consequences: judges rule on the whole exchange; workers reason over the full arc (recent
table + recallable earlier lines) instead of a 6-line window; Mystery Roots' convergence is
real on the live path, not hollow; and the cast can't self-cascade a turn to death. All
additive and offline-reproducible — `agent.thought` privacy is preserved and the
deterministic stub path is unchanged.

## References

- `src/core/context.py` — `ContextBuilder._blackboard_block`
- `src/core/conductor.py` — `_run_agent` isolation, `agent_errors`
- `src/core/memory.py` — index degradation, `_displayable`
- `src/core/structured.py` — reasoning strip, balanced-object scan, salvage
- `config/scenarios/the-steeped.yaml`, `config/agents/spy-*.yaml`
- Follow-up (recall the whole discussion): `src/core/memory.py` (`_GLOBALLY_VISIBLE` /
  `_REFLECTION_VISIBLE`), `src/core/context.py` (role-aware discussion block + dedup),
  `src/core/conductor.py` (`_notify_subscribers` no self-trigger), `config/agents/clue-gatherer.yaml`,
  `config/agents/devils-advocate.yaml`; tests in `test_memory.py` / `test_context.py` /
  `test_salience_memory.py`.
- Builds on ADR-0006 (ContextBuilder owns prompt assembly), ADR-0016 (Instructor structured
  output), ADR-0018 (memory index is a derived lens).
