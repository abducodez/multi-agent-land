# ADR-0002: Build the First Submission Around Gradio

## Status

Accepted

## Context

The hackathon explicitly judges Gradio app polish. A separate frontend stack could be powerful, but it would split attention before the core experience is delightful.

## Decision

Use Gradio as the first app shell and push it with custom CSS, strong defaults, and a stage-like layout.

## Consequences

- Faster path to a demoable app.
- Better alignment with judging criteria.
- More limited UI primitives than a bespoke frontend.
- If the project later needs a richer web app, the event/projection architecture keeps that migration feasible.

