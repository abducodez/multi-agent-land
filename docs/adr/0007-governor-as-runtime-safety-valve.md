# ADR-0007: Governor as Runtime Safety Valve

## Status

Accepted

## Context

A multi-agent scenario where many small models can post events indefinitely
is exactly the topology that produces runaway inference cascades and surprise
bills.  The conductor needs a mechanism to enforce budgets without those limits
being hard-coded inside scenario or agent code.

## Decision

`Governor` is a stateful collaborator injected into the conductor.  It tracks
calls-per-turn, total calls, and turn count, and raises `BudgetExceeded` if any
cap is exceeded.  The conductor calls `governor.begin_turn()` + `governor.check()`
before each scheduled agent and `governor.record_call()` after.

Caps are configuration: `Governor(max_turns=100, max_calls_per_turn=8,
max_total_calls=500)`.  The defaults are generous for interactive demo use and
can be tightened for cost-controlled production runs.

## Consequences

- Runaway scenarios cannot accidentally exhaust an API quota.
- Budget enforcement is decoupled from scenario logic.
- The governor is injectable and testable in isolation.
- `BudgetExceeded` is a named exception the UI can catch and surface gracefully.
