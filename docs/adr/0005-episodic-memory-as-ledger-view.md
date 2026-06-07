# ADR-0005: Episodic Memory as a Ledger View

## Status

Accepted

## Context

Small models have small context windows and drift out of character quickly.  
Each agent needs "memory" but storing separate per-agent state creates two
sources of truth and makes crash recovery harder.

## Decision

Agent memory is not a separate store. It is a **filtered view over the shared
ledger**, computed fresh each turn by `EpisodicMemory`. Each agent sees only
events it emitted itself plus globally-visible event kinds (`world.observed`,
`judge.verdict`, `user.injected`, `run.started`). The window is capped (default
8 events) to stay within small-model context budgets.

## Consequences

- Memory is always consistent with the ledger — no sync bugs possible.
- Crash recovery is free: reload the ledger, rebuild the view.
- Memory "recall" is a pure function of events (trivial to test, deterministic).
- Agents cannot see each other's private thoughts, enforcing cognitive privacy.
- Richer retrieval (semantic search, salience scoring) can be added later as an
  upgraded `EpisodicMemory` implementation without changing the agent protocol.
