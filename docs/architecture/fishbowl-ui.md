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

## The three tabs

Fishbowl is a `gr.Blocks` with a top bar and a `gr.Tabs` holding **The Lab**,
**The Show**, and **Hall of Fame**. A per-session `gr.State` carries the live
`FishbowlSession` and the play-head, so concurrent visitors never share a world.

### The Lab — compose a run

`lab.py` (`build_lab`) is the full interactive composer (ADR-0021, decision 4): a
scenario grid, premise / seed / world text fields, a narrator `gr.Dropdown`, a
**per-cast model picker**, a judge `gr.Group`, a tools `gr.CheckboxGroup`, and budget
`gr.Number`/`gr.Slider` controls.

The cast picker is a `@gr.render` over the scenario: one row per player (name + a model
`gr.Dropdown`), re-rendered as the cast changes.  Crucially, **every dropdown offers only
the models actually hosted on Modal** — its choices come from `modal_catalogue.entries()`
(the catalogue is the single source of truth and loads offline), so you can't cast a model
that isn't deployable.  Each pick writes the chosen endpoint slug into a `cast_models`
`gr.State` (`{agent_name: endpoint_key}`); the Judge gets its own catalogue dropdown in
§04.  "Surprise me" rerolls a cast; **"Summon" makes the choice real**: `collect_world_config`
maps each selection onto the agent's `model_endpoint` (ADR-0022), runs the per-run
`WorldConfig` through `validate_world()` (ADR-0011), and `Registry.from_world()` builds a
`Conductor` on the exact same engine path as a config-file run — so the model you pick is
the model that speaks (offline → the deterministic stub, demo still reproducible).  A bad
compose degrades to the scenario's default cast, so Summon never breaks the demo.

### The Show — watch it unfold

`show.py` (`build_show`) is the timer-driven theater: `gr.HTML` panels for the stage,
feed, meters, and verdict; a layout selector (`gr.Radio`: Constellation / Feed /
Split); a **"Read their minds" `gr.Checkbox`**; a transport (scrubber `gr.Slider`,
⏮ / ▶ / ⏭, speed `gr.Radio`, and a `gr.Timer`); and a poke strip that injects labelled
disturbances. `build_show` returns component handles only — every callback is wired in
`app.py`.

### Hall of Fame — the permanent record

`hall_of_fame.py` (`build_hall_of_fame`) is a read-only tab backed by the dedicated
`leaderboard_entries` table ([ADR-0035](../adr/0035-hall-of-fame-leaderboard.md)).
On each render it calls `make_leaderboard_store().entries()` to fetch the rows, then
passes them to the five aggregation functions in `src/core/leaderboard.py`. It never
reads the event ledger and never touches the live `Conductor` or the session state.
The `events` log stays the trace; this table is the scoreboard — linked back via
`run_id` for replay, not duplicated.

- **Scenario picker** (`gr.Dropdown`) filters all tables to one scenario. The "All
  scenarios" view is the model leaderboard's default — the cross-scenario headline.
- **Sessions table** (`gr.Dataframe` of `SessionRow`) lists every finished competitive
  run: date, cast summary, winner, winning model endpoint, turns, tokens, and end
  reason. Newest first.
- **Replay button** — each row carries a run id; clicking it calls `load_replay` from
  `src/ui/fishbowl/archive.py` (ADR-0027) and hands the `ReplaySession` to The Show.
  No new transport mechanism; replay is the same path the Archive drawer uses.
- **Model leaderboard** (`gr.Dataframe` of `ModelRow`) — plays, wins, win rate, and
  scenarios per model endpoint. This is the headline demo artifact: a single ledger
  fold turns "MiniCPM-8B has beaten Gemma-12B 7–3 at Debate Duel" into a table cell.
- **Agent / fairness tables** (`gr.Dataframe` of `AgentRow` and `SeatRow`) — per-seat
  win rates for the selected scenario. Judges and other non-seat cast members are
  excluded from `SeatRow` counts so the asymmetry is honest, not hidden.
- **Headline** — the `headline()` projection renders a single prose sentence above the
  tables when the ledger holds ≥1 symmetric-seat scenario with ≥2 models having won.
  Returns `None` at app start (no runs yet) and renders nothing; the UI handles both.

The Hall of Fame refreshes on tab focus, not on a timer. It holds no derived state —
every render is a fresh projection fold.

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

## Curtain call — "Start judging" + limit-reached verdict

The show resolves on a **judge's ruling**, not a silent halt. Two triggers bring on the
judge:

- **The visitor presses "⚖ Start judging"** (`show.py` → `judge_btn`), the amber pill in
  the transport. The app shell's `start_judging` handler stops autoplay (the halt-tail
  kills the `gr.Timer`) and calls `session.force_verdict()`.
- **A budget/turn limit ends the cast's run.** `on_tick` already stops on a tripped
  governor or the tick-cap backstop; when that happens and the cast has a judge that
  hasn't ruled, it brings the judge on instead of painting the bare `⛔ STOPPED` banner.

`FishbowlSession.force_verdict()` delegates to **`Conductor.force_verdict()`**, which:

1. **silences the cast** — drains `_pending` + `_trigger_queue` so no further competitor
   speaks after the curtain call;
2. **runs the judge(s) un-gated** (`role: judge` agents) — `_run_agent(..., check_budget=False)`
   so a verdict lands *even when the very budget that ended the show is spent*. The judge
   reads the whole run (`recent_events = events_for_run(run_id)`) and emits one
   `judge.verdict` carrying a `winner` (via the `judged-competition` handler offline);
3. is **idempotent** — a run that already has a verdict returns it unchanged.

The session then `finalize("verdict")`s the run. When the limit path had already
finalized the run `"budget"` with no winner, `Conductor.finalize` appends a **corrective
`run.finished`** carrying the verdict + winner (its one scoped exception to idempotency);
`run_index` folds `run.finished` last-wins, so the leaderboard attributes the ruling, not
the truncation. A cast with **no judge** (e.g. Oracle Grove) can't be forced — the click
halts visibly with a banner instead, and nothing is fabricated.

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
| `secret` | `str \| None` | audience-only ground truth for a hidden-word run (Twenty Sprouts); `None` otherwise. Rendered on the stage core, never placed in an agent prompt |
| `secret_holder` | `str \| None` | the actor holding `secret` (e.g. `secret-keeper`), or `None` |
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
- [ADR-0035](../adr/0035-hall-of-fame-leaderboard.md) — Hall of Fame: dedicated
  `leaderboard_entries` table detached from the event ledger; the five aggregation
  functions in `leaderboard.py` and the replay reuse via `run_id`.
