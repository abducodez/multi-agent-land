# ADR-0009: Open, Format-Validated Event Kinds

## Status

Accepted

## Context

The event schema is one of the four stable contracts.  Originally `kind` was a
closed `Literal` of six strings.  That made the *schema itself* a bottleneck for
modularity: a new scenario could not mint `clue.found` or `image.generated`
without editing `src/core/events.py` — the exact engine edit the architecture
promises to avoid.  Worse, the memory importance table already referenced kinds
(`agent.reflected`, `hypothesis.proposed`, …) that the closed `Literal` would
have rejected at construction time.

## Decision

Make `kind` an **open but format-validated** string.  A kind must match a
lowercase, dot-namespaced shape (`^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+$`), e.g.
`agent.spoke`, `clue.found`, `episode.published`.  Malformed kinds (no dot,
uppercase, symbols) still raise.

Authority moves to where it belongs: the schema validates *shape*; each agent's
`manifest.may_emit` validates *who may emit what*.  `CORE_EVENT_KINDS` records the
kinds the engine special-cases for projection/importance defaults, but it is a
default set, not a gate.

## Consequences

- A scenario adds new event kinds with zero engine edits — pure config.
- The generic projection fallback renders any text-bearing custom kind on stage,
  so drop-in agents are visible without touching the projection either.
- "New kinds additive only" becomes structurally true, not a manual discipline.
- Validation is weaker (shape, not membership), but `may_emit` is the real
  safety boundary and is enforced per turn in `ManifestAgent`.
- Persisted events keep `schema_version` for deliberate evolution.
