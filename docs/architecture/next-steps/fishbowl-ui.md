# Fishbowl UI — Assessment & Plan of Record

> **Status: ◐ In progress — Phases 0–3 shipped (the data foundation + the Gradio
> shell); Phase 4 (polish & prize lanes) pending.** Decisions locked 2026-06-08. The
> binding decision is [ADR-0021](../../adr/0021-fishbowl-ui-gradio-presenter.md). This
> page is the assessment and phased plan.
>
> **✅ Realized — see the as-built companion
> [architecture/fishbowl-ui.md](../fishbowl-ui.md)** for the shipped two-tab theater,
> the `gr.HTML` + `gr.Timer` render loop, the hybrid transport, and the `view_model_at`
> snapshot contract.

## Goal

Port the `ui/raw/` **Fishbowl** prototype into the Gradio app — a two-tab theater
(**The Lab** to compose a run, **The Show** to watch it) whose centerpiece is the
**say-vs-think MindCard** and the "Read their minds" toggle — **without changing the
engine** (`src/core`, `src/agents`, `src/models`, `src/scenarios`).

**Acceptance criteria:**

- The Show renders the ledger three ways — MindCards (Constellation), transcript
  (Feed), and omniscient table (Split) — driven by `gr.HTML` re-rendered on a
  `gr.Timer`.
- "Read their minds" reveals a per-agent private `thought`; `mood` drives the avatar.
  Offline (no API key) the deterministic stub still produces `thought`+`mood`.
- The transport scrubs the ledger (hybrid play-head); poke buttons inject events; the
  verdict flips the cards.
- The Lab composes a cast/judge/tools/budget, validates it with `validate_world()`,
  and launches a run.
- The engine is untouched; `tests/test_modularity.py` and the suite stay green; the
  no-API-key demo is reproducible.

## What the design is

`ui/raw/` is a working React prototype. Event vocabulary (`ui/raw/data.js`):

```
say     { agent, said, thought, mood }   # the say-vs-think pair — "the whole point"
narrate { voice, text }                  # narrator persona (doc / noir / bard / hype)
poke    { label, text }                  # a labelled world disturbance
verdict { text, reveal:[{agent, secret, role}] }   # card-flip reveal
```

`The Show` (`ui/raw/show.jsx`) derives per-agent state by replaying the ledger up to a
step (`deriveState`, `show.jsx:7`) and renders flippable `MindCard`s whose mood drives
the avatar face, colour, and animation (`ui/raw/shared.jsx:32`). The prototype's
"Gradio map" hint badges already annotate the intended components (`gr.Dropdown`,
editable `gr.Dataframe`, `gr.CheckboxGroup`, and **"gr.HTML re-rendered by gr.Timer"**
for the stage).

## What the engine already gives us (read surface)

| Surface | Where | Shape |
|---|---|---|
| Raw events | `conductor.ledger.events` | `tuple[Event, ...]`; `Event.payload` is free-form `dict` (`events.py:53`) |
| Stage view | `conductor.projection` → `rebuild_stage(events)` | pure function of the log (`projections.py:44`) |
| Real budget | `conductor.governor.stats` | `total_tokens`, `spend_usd`, `current_turn`, `total_calls` |
| Cast metadata | `scenario.agents[i].manifest` | `name, role, persona, model_profile, may_emit, tools` |
| Profile → model | `registry.build_router().describe()` | `{tiny→…, fast→…, …}` |
| Config validation | `validate_world / scenario / agent` | Pydantic; the "config as data" surface (ADR-0011) |
| Streaming delta | `Observer` / `ViewDiff` | `observer.py`; see [observer-pattern.md](../observer-pattern.md) |

## The gap (design need → engine today → modularity-preserving bridge)

| # | Design needs | Engine today | Bridge (no core breakage) |
|---|---|---|---|
| **G1** | Per-agent state `{said, thought, mood, spoke}` | Flat `agent_notes[-8:]`, lumps thought+spoke (`projections.py:27`) | **New pure projection** `derive_cast_state(events[:k], cast)` in the presenter (mirrors `rebuild_stage`). |
| **G2** | Mood (7 states → faces/colours/anim) | Does not exist | Agents emit `mood` via `output_extra_fields` (`manifest.py:116`); stub synthesizes. |
| **G3** | Say + think paired per utterance | Separate kinds; thought is in the ledger | Pair via a `thought` extra field on `agent.spoke` (or adapter pairs latest thought+spoke). |
| **G4** | Narrator voice | `world.observed {text}` | Optional `voice` payload field; default per scenario. |
| **G5** | Verdict reveals | `judge.verdict {text}` | Optional `reveal` payload field (ADR-0009). |
| **G6** | Poke label | `user.injected {text}` | Optional `label` field; default `"DISTURBANCE"`. |
| **G7** | Agent hue + archetype | `name, role, persona` | Derive from name/persona, or add optional manifest fields (default `None`). |
| **G8** | Friendly model label + tier dot | `model_profile` + `router.describe()` | Map profile → tier → colour in the adapter. |
| **G9** | Token/round meters | `governor.stats`, governor `max_turns` | **Direct read — the engine is *better* here** (prototype faked tokens). |

**G1 is the only real architectural gap, and it is a pure ledger view** — it belongs in
the presenter, not the core.

## Locked decisions (see ADR-0021)

1. **Render** — `gr.HTML` + `gr.Timer` (native inputs for the Lab; timer-driven HTML
   stage for the Show). CSS / 3D-flip / animations port ~verbatim.
2. **Timeline** — **Hybrid**: play-head `k` vs generation-head `N`; scrub-back is a
   pure prefix view, play-at-head steps the conductor, play-behind replays forward.
3. **Mood/thought** — **agents emit them** (additive `output_extra_fields`); stub
   synthesizes so the offline demo shows the mind-reader. Makes "AI is load-bearing"
   real, not faked.
4. **Lab** — **full interactive composer**: editable cast (model → profile map +
   per-agent temperature override), judge, tools, budget → per-run `WorldConfig` →
   `validate_world()` → `Conductor`.


## Implementation plan (phased)

Each phase is shippable and keeps the no-API-key stub working and the suite green.

- **Phase 0 — Foundation ✅ (shipped).** `src/ui/fishbowl/`: `derive_cast_state` (G1) +
  `adapter` (hue/tier/voice/mood + say/narrate/poke/verdict mapping, G7/G8) +
  `view_model_at` (prefix-replay snapshot, real tokens/rounds from `governor.stats`, G9).
  Pure, no Gradio. Covered by `tests/test_fishbowl.py` (prefix replay `k=0..N`, unknown
  actor/kind fallbacks).
- **Phase 1 — Triples are real ✅ (shipped).** Cast manifests declare
  `output_extra_fields: [thought, mood]` (G2/G3) plus optional `hue`/`archetype` (G7);
  the deterministic stub is now schema-aware and synthesises `thought`/`mood` offline, so
  the ledger carries the say-vs-think pairing with no API key (proven by
  `tests/test_fishbowl.py::TestOfflineEmitsMoodAndThought`). `inject_user_event` gained an
  optional `label` (G6); the adapter assigns a per-scenario narrator `voice` (G4) and
  reads an optional verdict `reveal` (G5) when present. Additive; 277 tests green.
- **Phase 2 — The Show ✅ (shipped).** `gr.HTML` + `gr.Timer` stage with the hybrid
  transport; Constellation, Feed, and Split layouts; the play-head state machine in
  `gr.State`; poke strip → `inject_user_event` (with `label`, G6); verdict banner +
  card-flip from `reveal` (G5). Ported CSS / 3D flip / CRT layers. See the as-built
  [architecture/fishbowl-ui.md](../fishbowl-ui.md) (`show.py`, `render/*`).
- **Phase 3 — The Lab ✅ (shipped).** Scenario grid + premise + seed/world + narrator;
  editable cast `gr.Dataframe` (model → profile map + per-agent temp override); judge
  `gr.Group`; tools `gr.CheckboxGroup`; budget `gr.Number`/`gr.Slider` → per-run
  `WorldConfig` → `validate_world` → `Conductor`. "Surprise me" reroll. See `lab.py`.
- **Phase 4 — Polish & prize lanes.** Optional `gr.Server` + mounted React for the
  Off-Brand award (reuses the Phase-0 view-model as JSON); "Export the saga" → HF
  trace; write the as-built `architecture/fishbowl-ui.md`.

## Modularity invariant

The engine never imports the UI. All new code lives under `src/ui/` and `app.py` and
depends only on the engine's public read surface. New data rides on `Event.payload` /
`output_extra_fields` / optional defaulted manifest fields — no event kind is removed or
repurposed, so `tests/test_modularity.py` and the existing suite stay green by
construction. This realizes the "Phase 3" rendering upgrade in
[observer-pattern.md](../observer-pattern.md) and the richer-web-app migration
anticipated by [ADR-0002](../../adr/0002-gradio-first.md).
