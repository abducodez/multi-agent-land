# ADR-0021: Fishbowl UI — a Gradio Presenter Layer over the Ledger

## Status

Accepted — decisions locked 2026-06-08. Implementation pending; the phased plan of
record is [next-steps/fishbowl-ui.md](../architecture/next-steps/fishbowl-ui.md).
Extends [ADR-0002](0002-gradio-first.md) (which anticipated this migration) and
realizes the "Phase 3" upgrade path documented in
[observer-pattern.md](../architecture/observer-pattern.md).

## Context

`ui/raw/` holds a complete React prototype — **Fishbowl** — a two-tab theater:
**The Lab** (compose the cast, judge, tools, and budget) and **The Show** (replay the
ledger as flippable *MindCards*, a narrator feed, and an omniscient split view). Its
centerpiece is the **say-vs-think** card: every utterance pairs a public `said` with a
private `thought` and a `mood`, gated behind a "Read their minds" toggle. ADR-0002
chose Gradio for the first shell and explicitly anticipated this moment: *"if the
project later needs a richer web app, the event/projection architecture keeps that
migration feasible."* This ADR records how we cash that in without disturbing the four
stable contracts.

The engine is already decoupled from rendering. Agents only append to the ledger; the
UI only reads `conductor.projection` (a pure function of the log,
`src/core/projections.py`), `conductor.ledger.events`, and `conductor.governor.stats`;
`src/ui/render.py` turns typed engine objects into display strings with **zero Gradio
imports in the engine**; the `Observer` documents a delta-streaming path. The
modularity invariant is test-enforced (`tests/test_modularity.py`).

The design needs data the engine does not project today:

- **per-agent current state** `{said, thought, mood, spoke}` — the engine projects a
  flat `agent_notes` list, not a per-agent view (`src/core/projections.py:13`);
- **mood**, a paired **thought**, a narrator **voice**, verdict **reveals**, and poke
  **labels** — none of which exist as engine concepts today.

## Decision

Add a **presenter layer** under `src/ui/` that depends only on the engine's
public read surface, and express the new data through extension points the contracts
already provide. Four choices are locked:

1. **Render with `gr.HTML` + `gr.Timer`.** The Lab uses native Gradio inputs; the Show
   is a single timer-driven `gr.HTML` stage (the prototype's own annotations call for
   "gr.HTML re-rendered by gr.Timer"). The prototype's CSS — including the 3D card flip
   and all avatar/CRT animation — is CSS-only and ports nearly verbatim.

2. **Hybrid timeline.** The transport tracks a *play-head* `k` against the
   *generation-head* `N = len(events)`. Scrubbing back is a pure prefix view
   (`rebuild_stage(events[:k])` and the new cast-state projection both take an events
   slice). "Play" at the head steps the conductor (append; `N` grows); "play" behind
   the head replays the existing prefix at the chosen speed.

3. **Agents emit mood and thought.** `thought` and `mood` ride on `agent.spoke` as
   `manifest.output_extra_fields` (`src/core/manifest.py:116`, ADR-0009 open payload);
   the narrator `voice` rides on `world.observed`; the verdict `reveal` on
   `judge.verdict`; the poke `label` on `user.injected`. All are *optional payload
   fields* — no new kinds, no removed kinds. The deterministic stub synthesizes
   `thought`+`mood` so the offline, no-API-key demo still shows the mind-reader working,
   keeping the marquee feature genuinely model-driven rather than faked.

4. **The Lab is a full interactive composer.** It edits an in-memory config (cast,
   per-agent model and temperature, judge, tools, budget), validates it with
   `validate_world()` (ADR-0011), and builds the run's `Conductor` from it. The Lab's
   abstract models map onto the existing four profiles (ADR-0010); per-agent
   temperature is carried as a per-run override (today temperature is per-*profile* in
   `config/models.yaml`).

**New ledger view, not new store.** Per-agent state is a pure projection
`derive_cast_state(events[:k], cast)` living in the presenter — the same "memory/UI is
a derived view of the ledger" discipline as ADR-0005: rebuildable and non-authoritative.

**Contract additions are additive only.** Optional `hue`/`archetype` fields may be
added to `AgentManifest` (default `None`, pure presentation). No event kind changes
shape; existing payloads are a subset of the new ones. The dependency arrow stays
one-way: `src/ui/` → engine, never the reverse.

## Consequences

- The engine packages (`src/core`, `src/agents`, `src/models`, `src/scenarios`) need no
  changes to render the Show; `tests/test_modularity.py` and the existing suite stay
  green by construction (new payload fields are optional; the projection's generic
  `text`-payload fallback at `src/core/projections.py:36` already renders unknown
  shapes).
- The presenter (`adapter` + `cast_state` + `view_model`) is **transport-agnostic**, so
  a later graduation to a mounted custom frontend (`gr.Server`, the "Off-Brand" lane)
  reuses it as a JSON endpoint rather than a rewrite.
- The only new glue near the engine is the Lab's per-agent temperature/model override;
  it is additive (a per-run router/manifest override via Pydantic `model_copy`) and
  strengthens the ADR-0011 "config a UI can compose and validate" story rather than
  bypassing it.
- Real token/round meters come free from `governor.stats` — the prototype faked them.
- Follow-ups: an as-built `architecture/fishbowl-ui.md` once shipped; "Export the saga"
  maps to a Hugging Face trace export (the ledger *is* the trace); judge
  "policy/strictness" need an engine meaning or remain presentation until then.

## Reconciliation with the four contracts

The roadmap's stability guarantee permits *additive* changes to the four contracts.
This ADR stays inside it: event **kinds** are unchanged (new data is optional payload
under ADR-0009); the **ledger API** is untouched; the **manifest** gains only optional,
defaulted fields; the **tool contract** is unchanged (the Lab edits grant lists, it
does not alter enforcement). Everything else — the presenter, the HTML, the Gradio
shell — is the hot-swappable rendering layer the architecture already says is free to
change.
