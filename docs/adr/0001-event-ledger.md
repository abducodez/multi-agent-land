# ADR-0001: Use an Append-Only Event Ledger

## Status

Accepted

## Context

The project needs multiple small agents to communicate indirectly, remain observable, and recover from crashes. Direct agent-to-agent calls would make behavior hard to inspect and hard to replay.

## Decision

Use an append-only event ledger as the source of truth. Projections derive world state, memory, UI state, and statistics from the event stream.

## Consequences

- Agent behavior can be inspected and replayed.
- The UI can show the system thinking without coupling to agent internals.
- Future persistence can move from memory to Postgres without changing scenario semantics.
- Schema evolution must be handled deliberately because old events remain valuable.

