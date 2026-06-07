# ADR-0003: Prefer Small Specialist Agents

## Status

Accepted

## Context

The hackathon asks builders to think small and stay under 32B parameters. Large generalist agents would undermine the theme and reduce prize fit.

## Decision

Model the experience as a cast of small specialist agents. Each role gets narrow context, a clear output shape, and a visible contribution to the ledger.

## Consequences

- Better fit for <=32B and <=4B model modes.
- More visible agentic behavior for judges.
- Requires a judge/conductor loop to avoid incoherent drift.
- Makes model provider swaps easier because roles declare capabilities rather than concrete providers.

