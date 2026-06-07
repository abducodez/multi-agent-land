# ADR-0004: Maintain a Build Journal and Living Technical Blog

## Status

Accepted

## Context

The project needs to be judged later by humans and Codex-like agents. A clear trail of decisions, learnings, and implementation progress will improve evaluation and demo preparation.

## Decision

Keep lightweight journal entries in `docs/journal/` and generate a living technical blog post at `docs/blog/building-in-public.md`.

## Consequences

- Progress is captured while context is fresh.
- Demo narrative and postmortem material accumulate automatically.
- The journal should stay concise so it does not become process drag.

