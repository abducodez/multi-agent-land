# The Wood Has Grown: A State of the Engine

*What we've built, what broke in the best possible way, and where the forest theater goes next.*

---

The elevator pitch hasn't changed: a forest where small specialist AI models — each one
≤ 32B parameters — never call each other. They post typed events to a shared append-only
ledger, and every view of the world (stage text, agent memory, the scrub-anywhere replay,
the exported trace) is a pure projection of that log. One engine, one idea.

What has changed is everything built on top of it. Since [the original engine post][1]
explained the four core abstractions — ledger, conductor, agents, projections — the
project has grown from a proof of concept into something resembling a real arena. Eight
competitive scenarios. A multi-model catalogue that can place four different sponsor
models in a single cast. A Fishbowl UI with MindCards and a mind-reader. Seven hundred
and forty-one passing tests with no mocks and no API key required. This is the next
chapter.

[1]: engine-architecture.md

---

## What's been built on the foundation

### The unfair advantage: per-agent model casting

Every agent in the cast declares a logical tier — `tiny`, `fast`, `balanced`, or
`strong` — and a `ModelRouter` binds that tier to a concrete small model at run time.
That was ADR-0010. The upgrade (ADR-0022) adds an optional `model_endpoint` field to each
agent manifest: a direct catalogue key that bypasses the tier entirely and pins a specific
model. The result is that a single cast can hold several different sponsor models at once,
legitimately running in the same show.

```yaml
# An agent manifest can pin a concrete model instead of a tier:
name: house-judge
role: judge
model_endpoint: nemotron-cascade-14b   # a specific catalogue key, not a tier
output_extra_fields: [mood, winner]
```

This is the prize-strategy lever. One submission can route a spy to MiniCPM, a judge to
Nemotron, and a debater to Gemma without touching the engine — just YAML. The Fishbowl
Lab's cast picker is now load-bearing, not cosmetic: the model you choose is the model
that speaks (offline it drives the deterministic stub's variant, so demos stay
reproducible).

### A model catalogue that serves and routes

The engine and the serving layer used to describe the same models in two separate places,
with nothing connecting them. A rename in `modal/catalogue.py` didn't update the URL in
`config/models.yaml`, and the drift always showed up mid-demo. ADR-0019 fixed this: one
catalogue, written in plain stdlib Python, shared by both sides. The engine derives each
profile's binding — model string, endpoint URL, API key — from the same file the vLLM
apps deploy from. Adding a model is one line in `modal/catalogue.py`; the engine picks it
up with no parallel edit.

The serving backend story has also evolved from a single vLLM path into three:

| Backend | When it activates |
|---|---|
| Modal vLLM (ADR-0014, ADR-0034) | `MODAL_WORKSPACE` is set; seven models across three provider families (NVIDIA, OpenBMB, Google) |
| Hugging Face serverless (ADR-0024) | `HF_TOKEN` set; for routing-class models |
| Local in-process `transformers` (ADR-0033) | HF ZeroGPU Space, or `LOCAL_INFERENCE=1`, or CUDA detected |

The local backend (ADR-0033, superseding the earlier llama.cpp design) deserves a note.
ZeroGPU grants a GPU only for the duration of a decorated function call, then reclaims it.
A long-lived HTTP server — llama.cpp, vLLM-as-a-server — can't hold the GPU between
requests; the model is structurally incompatible with that environment. The in-process
`transformers` backend wraps each forward pass in a `@spaces.GPU`-decorated function that
is a transparent no-op on dedicated hardware and a real GPU grant on ZeroGPU. No
environment-specific branching; the same binary runs everywhere.

The Modal serving layer itself was simplified in ADR-0034 after its snapshot-lifecycle
machinery grew to ~500 lines and became the primary source of deploy errors. The alpha GPU
snapshot system — clever, but fragile — was removed entirely. Cold starts now rely on
shared weight/compile caches on Modal Volumes plus a `MODAL_LLM_KEEP_WARM` demo-day
switch. Fewer moving parts; the thing that was failing is gone.

### Memory as layered ledger views

Agent memory never left the ledger. The architecture post explained the episodic layer
(ADR-0005); what's been added since is depth. The full stack now has three tiers:

- **Episodic** — a recency window of each agent's own events plus globally-visible kinds.
- **Salience** — ranks visible events by a composite score:
  `w·relevance(e, scene) + w·recency(e, turn) + w·importance(e.kind)`. User-injected
  events score highest. An old judge verdict that directly concerns the current scene
  outranks a recent but irrelevant greeting.
- **Reflection** — every N visible events, an agent compacts episodic memories into a
  single `agent.reflected` belief. One sentence replaces ten events in the context window;
  the belief is itself a ledger event, so memory can be recalled across arbitrarily long runs.

An optional semantic index (ADR-0018) upgrades the relevance term from keyword-Jaccard to
embedding similarity when `MEMORY_INDEX` is set — a derived, rebuildable lens that never
becomes a second source of truth. Default embeddings run locally via
`sentence-transformers/all-MiniLM-L6-v2`; the engine is fully off the grid by default.

### An arena: sessions, verdicts, and eight scenarios

The run lifecycle (ADR-0026) gave the ledger a proper beginning and end. Every run now
opens with an enriched `run.started` carrying the scenario name, the full cast→model
binding map, and a UUID; it closes with `run.finished` recording the reason (verdict,
budget, tick cap, or user stop), the winner, the winning model, and the turn/token cost.
The ledger accumulates many runs; `reset()` no longer wipes the database.

Structured verdicts (ADR-0029) made winners machine-readable. A judge emits a typed
`winner` field alongside the verdict prose, validated against the cast with one corrective
re-ask if needed. Where the scenario has a ground truth, a handler stamps the winner
in code — not prose. Per-user sessions and archive replay (ADR-0027) completed the
picture: each browser gets a stable session id, sees only its own runs, and can scrub back
through any past show without spending a single token. The scrub-anywhere replay is a pure
ledger prefix view — `rebuild_stage(events[:k])` — so it falls out of the same pure
projection function that drives the live show.

The result is eight scenarios, each arena-grade:

| Scenario | Competition kind | Winner decided by |
|---|---|---|
| 🕵 The Steeped (word-pair bluff game) | versus (teams) | code — handler checks the accusation |
| ❓ Twenty Sprouts (20 questions) | versus (teams) | code — handler reads the secret word |
| ⚔️ Debate Duel | versus (symmetric seats) | judge + offline text repair |
| 🎭 Beat Battle | versus (symmetric seats) | judge + offline text repair |
| 🔍 Mystery Roots | judged | judge + offline text repair |
| 💬 Open Table | judged | judge + offline text repair |
| 🍄 Thousand Token Wood | none (collaborative) | — |
| 🔮 Oracle Grove | none (tool showcase) | — |

### The Fishbowl UI

The Fishbowl (ADR-0021) is the theater front-end: a two-tab Gradio presenter built
entirely on the ledger's public read surface. The Lab lets you compose a cast, pick
per-agent models from the catalogue, set the judge, tools, and budget, then summon the
show. The Show presents each utterance as a **MindCard** — a flip card pairing a public
`said` with a private `thought` and a `mood`, gated behind a "Read their minds" toggle.
The play-head is a scrubber over `k ∈ [0, N]` where `N = len(events)`: behind the head is
a pure replay; at the head it drives the conductor forward.

The per-agent `thought` rides on the `agent.spoke` payload as an optional field
(ADR-0009 open payload — no new event kind). On reasoning models the `thought` is
captured from `reasoning_content` via the vLLM reasoning parser, so the mind-reader shows
a model's actual chain of thought, not a fabricated inner monologue. It reaches the
mind-reader's toggle and nothing else — a peer never reads another mind's thinking.

---

## Three hard lessons from shipping

### 1. The shared blackboard wasn't shared

This was the headline finding of the first live run. The stage projection exposes a
`current_scene` field (updated by `world.observed`) and an `agent_notes` list (updated by
`agent.spoke`). The bug: `ContextBuilder` injected `current_scene` into every agent's
prompt but never injected `agent_notes`. And `agent.spoke` was not in the globally-visible
event kinds, so it didn't reach episodic memory either.

Every agent was acting in a vacuum. It saw its own persona, the world text, and its own
past lines. A small model with nothing new to react to looped — same line, every turn,
only one agent ever speaking. This pattern was completely hidden by the offline stub,
whose deterministic responses don't depend on context shape at all.

The first fix: `ContextBuilder` now injects a `WHAT'S BEEN SAID` block from
`projection.agent_notes`, with a "react to this / add a genuinely new line / never repeat"
nudge. Workers see the recent live table. The second, deeper fix (added after auditing
every scenario): `agent.spoke` and `oracle.spoke` were added to `_GLOBALLY_VISIBLE` in
`src/core/memory.py`, so public speech is recallable across the whole run, not just the
current round's blackboard tail. The private `agent.thought` stayed out — shared speech,
private thinking.

The judge problem was starker. A judge fires late with no own-events yet, so before the
fix its salience candidates were exactly zero spoken lines. Measured across shipped casts:
`open-table` judges saw 6 of 18 lines; `the-steeped` host saw 6 of 16 clues. The fix is
role-aware context assembly — workers get `WHAT'S BEEN SAID` (the recent reactive table);
judges get `THE EXCHANGE TO JUDGE`, the complete ordered public transcript. After the
change, every judge sees 100% of the discussion.

The self-cascade guard was a quiet bonus fix here: an agent that both subscribes to and
emits `agent.spoke` would re-trigger itself, burning through the per-turn call cap before
the judge fired. `Conductor._notify_subscribers` now never queues an agent for its own
event.

### 2. Code owns ground truth; the model owns judgment

Hidden-word games — The Steeped's bluff game, Twenty Sprouts' guessing game — crystallised
a principle that sounds obvious but took a live failure to enforce properly.

The original approach: deal the secret word in the `default_seed` text (globally visible)
and let the judge reason about who held it. The model knew the answer before the game
started. So did every player. So did the audience.

The fix: the secret word is dealt by a handler in code, carried on a private `secret`
payload key that is never an event's `text`. The context/memory builder surfaces only
`text` (and never the raw payload), so the word rides the ledger as unambiguous ground
truth without ever reaching a prompt. When the guesser finally names the word, a handler
reads the ledger, compares strings, and stamps the winner — not the model. The model
writes the drama; the code writes the scoreboard.

```python
# src/agents/twenty_sprouts.py — the judge decides in code, not prose
caught = secret.lower() in set(_WORD.findall(guess.lower()))
event.payload["correct"] = caught
event.payload["reveal"] = [{"agent": "secret-keeper", "secret": secret,
                            "role": "GUESSED" if caught else "KEPT SECRET"}]
return _GUESSER_NAME if caught else "secret-keeper"   # the winner is a name, decided here
```

An extra layer of defense: `clean_clue` in `src/core/structured.py` scrubs any sentence
containing a standalone ALL-CAPS token (≥ 3 letters) from spoken output — because small
models will sometimes blurt the secret in caps ("Since COFFEE is common…") even when the
prompt says not to. The model doesn't need to be reliable about this; the code is.

### 3. Small models need the right context shape, not just a better prompt

The second-order failure after fixing the blackboard was that the cast went silent again —
almost every event became a raw fallback placeholder (`…`). The cause this time was token
budgets, not visibility.

Several models were served with a `reasoning_parser` that makes them think before
answering. The thinking counts against `max_tokens`. With `balanced` capped at 320 tokens,
the model truncated mid-thought and emitted an empty answer. Three of four players in The
Steeped were on that tier; the show died before it began.

The fixes were mechanical but instructive: raise token budgets on reasoning tiers to give
models room to finish thinking before answering; capture `reasoning_content` from the vLLM
channel as the mind-reader thought rather than discarding it; diversify the cast so no
single model tier can sink three agents at once. And add a no-repeat guard: small models
ignore "never repeat" instructions and loop verbatim across the whole cast. Token-set
Jaccard similarity (≥ 0.8 against recent ledger lines) now flags a repeat and skips the
turn, keeping the live path from degenerating while leaving the offline stub's curated
catalogue intact.

The broader lesson is that live behavior with real small models diverges sharply from
offline behavior with a deterministic stub. The stub is essential for reproducible demos
and CI; it just can't tell you whether your context shape is actually useful.

---

## What we're genuinely proud of

**The trace is the product.** The append-only ledger isn't just an architecture choice —
it's a shareable artifact. Every run is framed by `run.started` (scenario, full cast→model
binding map) and `run.finished` (winner, winning model, reason, turns, tokens). Export
that slice as JSONL and you have a self-describing agent trace ready to publish to the
HF Hub. No extra bookkeeping; the ledger was always keeping the record.

**Agents that never call each other.** No agent knows another agent exists. They post
events; the conductor routes; the projections observe. The cast for The Steeped is four
agents who have never spoken a line to each other — they speak to the ledger, which
speaks to the world. This isn't a constraint; it's the originality hook. The whole
multi-agent coordination emerges from typed events and pure projections, with no agent
framework, no shared memory store, and no message-passing protocol.

**Drop-a-YAML extensibility, test-enforced.** Adding a scenario is two YAML files and
zero engine edits. `tests/test_modularity.py` proves it — it builds every scenario config
and asserts the invariants hold. Eight scenarios, one engine, 741 green tests, and the
rule is enforced by a test that will catch the first time someone accidentally needs an
engine edit to ship a new world.

**Fully reproducible with no API key.** The deterministic stub produces fixed, structured
outputs for every event kind. The whole show runs on stage with no credentials, no
network, no GPU. The offline path isn't a reduced demo; it's a first-class product
constraint that shapes every design decision.

---

## What's next

A few things are close to done; a few are honest gaps we're naming clearly.

**The commentator booth.** A `booth-commentator` agent — model tier `tiny`, subscribes to
`agent.spoke` and `judge.verdict`, emits a new `commentary.note` kind — is a pure YAML
addition. It fires reactively, stays off the critical path (never blocks the show, never
pollutes other agents' memory), and gives the Tiny Titan lane a starring role: a ≤4B
model doing real color commentary on a live arena.

**Hall of Fame leaderboard.** The run lifecycle and structured verdicts are in place. The
leaderboard is a pure projection over the ledger — fold each `run.started` cast→model map
against its `run.finished` winner. No new storage. The headline
artifact: "MiniCPM-8B has beaten Nemotron-Cascade-14B 7–3 at Debate Duel" — and you can
replay every one of those sessions from the Archive.

**Published HF Space, exported agent trace, demo video.** The deliverables that win
prizes independent of code. The trace export is nearly free (the ledger is the trace;
`scripts/export_trace.py` is the last step); the HF Space needs a public deploy with the
ZeroGPU local backend wired in; the demo video is the thing that makes Community Choice
real.

**Off-Brand frontend.** ADR-0021 designed the presenter layer to be transport-agnostic —
it's a JSON source, not a Gradio dependency. The `gr.Server` path (the "Off-Brand" prize
lane) reuses that presenter as a mounted endpoint rather than a rewrite. The Fishbowl's
CSS — 3D card flips, avatar animations, CRT stage glow — is already written; it just
needs a different host.

**Known honest gaps.** Tool calls aren't yet ledger events — they happen inside an agent's
`act()` and leave no first-class trace. The hourly budget is a per-run token total, not a
true wall-clock cost cap (the USD rate is an approximation). The ZeroGPU free-tier quota
(~5 minutes GPU/day for authenticated users) limits live demo length on the public Space.
A fine-tuned specialist — train a house commentator on our own ledger data, publish on HF,
route via the local backend — is the highest-effort, highest-distinctiveness item on the
board, and it isn't started yet.

---

The forest theater is now a small arena. Eight worlds, a multi-model cast, an event
ledger that is also a shareable trace, and small models doing real work under a hard
32-billion-parameter ceiling. The ceiling is the point. Not because there's anything wrong
with larger models — but because the interesting design space opens up when you ask: what
does a specialist ≤4B model do better than a generalist 70B? Quite a lot, it turns out,
when you give it the right context shape and put it next to six equally small colleagues
who are each very good at exactly one thing.

More soon.
