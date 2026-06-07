# ADR-0006: ContextBuilder Separates Prompt Assembly from Agent Logic

## Status

Accepted

## Context

Without a dedicated prompt-assembly layer, each agent hard-codes how it blends
persona, world state, and memory into a prompt string.  That makes it impossible
to iterate on the prompt strategy without touching every agent, and impossible
to enforce a consistent structure across scenarios.

## Decision

Introduce `ContextBuilder` as a standalone collaborator.  Agents call
`ctx.build(agent_name=..., persona=..., projection=..., all_events=...)` and
receive a fully-formatted string.  The builder owns the layering order:
persona → current scene → episodic memory → visitor disturbances.

Agents are responsible only for the **persona string** and the **action they
emit**.  The builder is responsible for everything in between.

## Consequences

- Prompt structure is a single point of change.
- Adding a new memory layer (e.g. reflection summaries) touches only
  `ContextBuilder`, not every agent.
- Agents remain testable with a stub builder.
- The layering order is a documented, reviewable decision rather than implicit
  per-agent convention.
