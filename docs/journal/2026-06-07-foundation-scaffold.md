# Foundation Scaffold

Date: 2026-06-07

## Built

- Created a Gradio-first hackathon scaffold.
- Added an append-only in-memory ledger and stage projection.
- Added three deterministic tiny agents: scene whisperer, mischief critic, and pocket actor.
- Added ADRs, architecture notes, runbooks, prize strategy, and Codex judge rubric.

## Decisions

- Favor a runnable delightful vertical slice over a large platform skeleton.
- Keep the ledger visible because inspectability is both useful architecture and good demo storytelling.
- Preserve a deterministic stub model so the app can run locally before provider credentials exist.

## Learned

- The hackathon criteria strongly favor a playful Gradio surface, so the first architecture should support delight rather than distract from it.
- Prize stacking needs explicit tracking from the start, especially Tiny Titan, Best Agent, Off-Brand UI, and Best Demo.

## Next

- Improve the stage visuals and add richer visitor choices.
- Add a real small-model provider adapter.
- Export run traces for demos and blog posts.

