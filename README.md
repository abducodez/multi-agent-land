# Multi-Agent Land

Hackathon project for the **Thousand Token Wood** trail: a small-model, multi-agent interactive story engine where the AI is load-bearing for the experience.

The working thesis is:

> One tiny event-sourced engine can power many delightful worlds. The first world is a whimsical forest theater where small specialist agents write, judge, remember, and render strange interactive scenes.

## Hackathon Targets

- Delight first: strange, joyful interactions worth showing a friend.
- AI is load-bearing: agents create the evolving story, not just labels around static UI.
- Small models: keep every runtime model under the 32B parameter cap, with an optional <=4B Tiny Titan mode.
- Polished Gradio app: custom layout, live ledger, visible agent activity, and demo-friendly defaults.
- Prize stacking: aim for Thousand Token Wood, Community Choice, OpenAI Track, Tiny Titan, Best Agent, Off-Brand UI, Best Demo, and Judges' Wildcard.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

The app is intentionally usable with a deterministic local model stub first. Swap in real small-model providers through `src/models/provider.py` as the build matures.

## Repository Map

```text
app.py                      Gradio composition root
src/
  core/                     Event ledger, schemas, projections, conductor
  agents/                   Manifest-driven specialist agents
  scenarios/                Scenario configuration and seeds
  models/                   Small-model provider abstraction
  ui/                       Gradio rendering helpers
docs/
  vision.md                 One-page product and technical vision
  architecture/             System design, C4-style notes, sequence diagrams
  adr/                      Append-only Architecture Decision Records
  schema/                   Event and manifest contracts
  runbooks/                 Local dev, demo, recovery, observability
  strategy/                 Hackathon prize strategy and judging rubric
  blog/                     Technical blog posts built along the way
  journal/                  Daily build log entries
scripts/
  new_journal_entry.py      Creates dated build log entries
  snapshot_progress.py      Updates docs/blog/building-in-public.md from journal
modal/
  service.py                Reusable vLLM serving layer (OpenAI-compatible)
  registry.py               Declarative model catalogue, grouped by provider
  app_*.py                  One Modal app per provider (nvidia/openbmb/google)
```

## Development Loop

1. Build the thinnest vertical slice.
2. Record decisions in `docs/adr/`.
3. Capture learnings with `python scripts/new_journal_entry.py "What changed"`.
4. Regenerate the living technical blog with `python scripts/snapshot_progress.py`.
5. Keep scenarios modular: new worlds should be config and plugin files, not engine rewrites.

## Current Status

Phase 0 foundation is scaffolded. The next milestone is a polished vertical slice: three agents, one judge, one observer projection, and a Gradio experience that feels playful immediately.

