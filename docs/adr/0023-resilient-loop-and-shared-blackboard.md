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

## References

- `src/core/context.py` — `ContextBuilder._blackboard_block`
- `src/core/conductor.py` — `_run_agent` isolation, `agent_errors`
- `src/core/memory.py` — index degradation, `_displayable`
- `src/core/structured.py` — reasoning strip, balanced-object scan, salvage
- `config/scenarios/the-steeped.yaml`, `config/agents/spy-*.yaml`
- Builds on ADR-0006 (ContextBuilder owns prompt assembly), ADR-0016 (Instructor structured
  output), ADR-0018 (memory index is a derived lens).
