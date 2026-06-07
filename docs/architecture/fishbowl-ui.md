# Fishbowl UI — As-Built

> **Status: ✅ Built.** This is the realized companion to the plan of record
> ([next-steps/fishbowl-ui.md](next-steps/fishbowl-ui.md)) and the binding decision
> ([ADR-0021](../adr/0021-fishbowl-ui-gradio-presenter.md)). It documents the Fishbowl
> Gradio UI as it actually ships, not as it was planned. Phase 4 (the `gr.Server`
> Off-Brand frontend and the saga→HF trace export) is still pending.

Fishbowl is the live theater for Multi-Agent Land: a two-tab Gradio app that lets you
**compose** a run (The Lab) and **watch** it unfold (The Show) — flippable MindCards,
a narrator feed, and an omniscient split view, all driven off the append-only ledger.
Its centerpiece is the **say-vs-think** card: every utterance pairs a public `said`
with a private `thought` and a `mood`, gated behind the "Read their minds" toggle.

## At a glance

```
ui/raw/ (React reference prototype)
        │  ported
        ▼
src/ui/fishbowl/
  __init__.py                presenter exports + build_app/demo
  cast_state.py    ┐
  adapter.py       ├ presenter (pure, no Gradio) — the shipped read surface
  view_model.py    ┘   view_model_at(...) → one JSON-serialisable snapshot
  theme.py, assets/        FishbowlTheme + ported CSS / CRT head HTML
  render/
    avatar.py  mindcard.py  pure HTML renderers — each over a view-model slice
    stage.py   feed.py      (constellation/split, transcript)
    meters.py               (token bar, rounds, verdict banner)
  session.py               FishbowlSession — a live Conductor wrapper
  show.py                  The Show tab (gr.HTML stage + transport + gr.Timer)
  lab.py                   The Lab tab (cast/judge/tools/budget composer)
  app.py                   build_app() → gr.Blocks; wires every callback
app.py (repo root)         thin shim → from src.ui.fishbowl.app import demo
```

The dependency arrow is one-way: `src/ui/fishbowl/` reads the engine's public surface;
**the engine never imports the UI** (test-enforced by `tests/test_modularity.py`).

## The two tabs

Fishbowl is a `gr.Blocks` with a top bar and a `gr.Tabs` holding **The Lab** and
**The Show**. A per-session `gr.State` carries the live `FishbowlSession` and the
play-head, so concurrent visitors never share a world.

### The Lab — compose a run

`lab.py` (`build_lab`) is the full interactive composer (ADR-0021, decision 4): a
scenario grid, premise / seed / world text fields, a narrator `gr.Dropdown`, an
**editable cast `gr.Dataframe`** (per-agent model→profile map + temperature override),
a judge `gr.Group`, a tools `gr.CheckboxGroup`, and budget `gr.Number`/`gr.Slider`
controls. "Surprise me" rerolls a cast; "Summon" collects the inputs into a per-run
`WorldConfig`, runs it through `validate_world()` (ADR-0011), builds the `Conductor`,
and switches to The Show. The Lab's abstract model choices map onto the four engine
profiles (`tiny`/`fast`/`balanced`/`strong`, ADR-0010); per-agent temperature is a
per-run override, since temperature is otherwise per-*profile* in `config/models.yaml`.

### The Show — watch it unfold

`show.py` (`build_show`) is the timer-driven theater: `gr.HTML` panels for the stage,
feed, meters, and verdict; a layout selector (`gr.Radio`: Constellation / Feed /
Split); a **"Read their minds" `gr.Checkbox`**; a transport (scrubber `gr.Slider`,
⏮ / ▶ / ⏭, speed `gr.Radio`, and a `gr.Timer`); and a poke strip that injects labelled
disturbances. `build_show` returns component handles only — every callback is wired in
`app.py`.

## The render loop — `gr.HTML` + `gr.Timer`

The Show does not stream component diffs; it **re-renders one `gr.HTML` block** each
tick (ADR-0021, decision 1). The loop, wired in `app.py`, is:

1. A `gr.Timer.tick` (or a transport click, or a scrubber drag) fires with the session
   and the current play-head `k`.
2. The integrator calls
   `view_model_at(session.events, k, session.cast, scenario_name=…, goal=…, governor=session.governor, voice=…, token_ceiling=…, max_rounds=…)`
   to get one snapshot dict.
3. The `render/*` functions compose that dict into HTML — MindCards
   (`render/mindcard.py`, each wrapping an avatar from `render/avatar.py`) are laid out
   by `render/stage.py` (`render_constellation` / `render_split`), the transcript by
   `render/feed.py`, and the meters + verdict banner by `render/meters.py`.
4. The composed string is pushed into the `gr.HTML` value.

Renderers **never re-derive state** — they consume the snapshot dict. The presenter
(`cast_state` + `adapter` + `view_model`) is transport-agnostic, so the same snapshot
can later feed a `gr.Server` JSON endpoint (the Off-Brand lane) without a rewrite. This
realizes the "Phase 3" rendering upgrade anticipated in
[observer-pattern.md](observer-pattern.md).

## Hybrid transport — scrub vs play

The transport tracks a **play-head** `k` against the **generation-head**
`N = len(session.events)` (ADR-0021, decision 2):

- **Scrub back** (`k < N`) is a **pure prefix view** —
  `view_model_at(events, k, …)` clamps `k` to `[0, N]` and replays only `events[:k]`,
  exactly like `rebuild_stage`. No engine call; the world is not advanced.
- **Play at the head** (`k == N`) **steps the Conductor** — `session.step()` appends a
  turn, `N` grows, and the next tick renders the new prefix.
- **Play behind the head** replays the existing prefix forward at the chosen speed.

So "replay" and "live" are the same code path differing only in whether the tick
appends. Offline (no API key) the deterministic stub still produces every turn, so the
hybrid transport — and the whole demo — is reproducible on stage.

## Say vs think — the MindCard

Each MindCard (`render/mindcard.py`) shows a mind's front face (its public `said`) and,
when "Read their minds" is on, flips to reveal the private `thought` and `mood`. With
the toggle off the thought is sealed; a `panic` mood "leaks" a sliver regardless. The
final `judge.verdict` `reveal` flips the cards to expose each agent's secret/role.

The pairing rides on **optional payload fields** (ADR-0009, ADR-0021 decision 3):
`thought`/`mood` travel alongside `text` on `agent.spoke` via the manifest's
`output_extra_fields`; the deterministic stub synthesizes them offline. This keeps the
marquee feature genuinely model-driven rather than faked scaffolding.

### mood → avatar

`render/avatar.py` draws the SVG face; its expression and animation are driven by the
mood. The `adapter` defines the palette (`MOOD_META`) and normalizes any unknown mood
to `calm`:

| mood | label | role on the avatar |
|---|---|---|
| `thinking` | thinking | pondering, dim |
| `calm` | composed | steady (default / fallback) |
| `lying` | bluffing | shifty |
| `panic` | PANICKING | rattled, leaks thought |
| `smug` | smug | grinning |
| `truth` | sincere | open |
| `gossip` | scheming | conspiratorial |

A per-agent hue (`adapter.agent_hue`, the manifest's `hue` or a stable hash of the name)
colours the card via `oklch(...)`; a tier dot colours the model badge from
`adapter.TIER_COLOR` (engine profiles collapse onto the design's `fast`/`mid`/`deep`).

## The three layouts

The layout selector toggles how the same snapshot is drawn:

- **Constellation** — MindCards arranged in a ring around a scene "core" glyph
  (`render/stage.py:render_constellation`, taking the pre-rendered cards).
- **Feed** — the narrator transcript, one line per feed item with the narrator voice
  persona (`render/feed.py`).
- **Split** — an omniscient table of every mind's `said` vs `thought` side by side
  (`render/stage.py:render_split`).

The narrator persona comes from the `adapter`'s `VOICES` map
(`doc`/`noir`/`bard`/`hype`), defaulted per scenario by `scenario_voice` and
overridable in The Lab.

## `FishbowlSession` — the live-engine wrapper

`session.py` wraps a live `Conductor` so the UI never touches engine construction
directly. It builds the run from the engine's public factories
(`default_registry` / `build_scenario` / `build_router` / `make_ledger` /
`default_tool_registry`) and exposes exactly what the render loop needs:

- methods `reset(seed)`, `step()`, `inject(text, label)`;
- read props `events`, `cast` (the agent manifests), `governor`, `scenario_name`,
  `goal`, `token_ceiling`, `max_rounds` — the precise argument set for `view_model_at`.

It creates no Gradio components; it is held per-session in `gr.State`.

## The `view_model_at` snapshot contract

`view_model.view_model_at(events, k, cast, *, scenario_name="", goal="", governor=None,
voice=None, token_ceiling=None, max_rounds=None) -> dict` is the **single object every
renderer binds to**. It is a pure function of `events[:k]`. Top-level keys:

| key | shape | notes |
|---|---|---|
| `step` | `int` | the play-head `k`, clamped to `[0, total]` |
| `total` | `int` | `N = len(events)`, the generation-head |
| `scene` | `str` | from `rebuild_stage(prefix).current_scene` |
| `seed` | `str` | the run seed |
| `goal` | `str` | the shared goal |
| `cast` | `list[dict]` | one per mind (below) |
| `feed` | `list[dict]` | tagged feed items (below), each with `turn` |
| `voice` | `str` | the active narrator key |
| `voice_meta` | `{name, desc}` | the resolved narrator persona |
| `speaking_id` | `str \| None` | the head event's actor, when it just spoke |
| `verdict` | `{text, reveal, agent} \| None` | the latest `judge.verdict` |
| `rounds` | `int` | `1 + count(user.injected)` |
| `max_rounds` | `int \| None` | the round ceiling, if set |
| `tokens` | `int` | a text-based estimate through the prefix |
| `tokens_real` | `dict \| None` | `governor.stats` (real tokens/spend/calls) when present |
| `token_ceiling` | `int \| None` | the budget bar's max |

Each `cast[i]` is
`{id, name, archetype, hue:int, role, model_profile, tier, said, thought, mood,
mood_label, spoke:bool, speaking:bool}`.

Each `feed[i]` is tagged by `kind` (plus `turn`): `narrate{voice, text}` ·
`say{agent, said, thought, mood}` · `poke{label, text}` ·
`verdict{text, reveal[], agent}`.

`view_model_at` composes two pure helpers:

- `cast_state.derive_cast_state(events, cast_names) -> {name: CastMemberState}` — the
  G1 fix: the per-agent `{said, thought, mood, spoke, last_turn}` view the engine's flat
  `agent_notes` never gave us. Like `rebuild_stage`, it is a pure function of an events
  slice (`src/ui/fishbowl/cast_state.py`).
- `adapter` — the engine→design vocabulary: `agent_hue`, `agent_archetype`,
  `model_tier`/`TIER_COLOR`, `MOOD_META`, `VOICES`, `scenario_voice`, and
  `event_to_feed_item` (the say/narrate/poke/verdict mapping)
  (`src/ui/fishbowl/adapter.py`).

## Modularity invariant

All Fishbowl code lives under `src/ui/fishbowl/` (plus the root `app.py` shim) and
depends only on the engine's public read surface — `ledger.events`,
`conductor.projection` (`rebuild_stage`), `governor.stats`, agent manifests,
`build_router().describe()`, and `validate_world()`. New data rides on optional
`Event.payload` / `output_extra_fields` / defaulted manifest fields, so no event kind is
removed or repurposed. The engine packages (`src/core`, `src/agents`, `src/models`,
`src/scenarios`) need no changes to render the Show, and `tests/test_modularity.py`
stays green by construction.

## Related

- [ADR-0021](../adr/0021-fishbowl-ui-gradio-presenter.md) — the binding decision.
- [next-steps/fishbowl-ui.md](next-steps/fishbowl-ui.md) — the assessment and phased
  plan of record (the gap analysis G1–G9).
- [observer-pattern.md](observer-pattern.md) — the decoupled-rendering contract this
  realizes.
- [ADR-0002](../adr/0002-gradio-first.md) — chose Gradio and anticipated this migration.
