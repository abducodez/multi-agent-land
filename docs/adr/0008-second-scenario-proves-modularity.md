# ADR-0008: Second Scenario Proves Modularity

## Status

Accepted

## Context

A "modular engine" claim is only proven when it is reused.  Adding Mystery
Roots is not just a feature — it is the test that confirms the engine/scenario
boundary is correct.

## Decision

Add `src/scenarios/mystery_roots.py` as a structurally distinct second scenario:
- Different cognitive task (convergent mystery-solving vs. divergent world-growth)
- Different agent cast (ClueGatherer, HypothesisFormer, DevilsAdvocate, MysteryJudge)
- Different scheduling policy (4-phase cycle vs. even/odd/triple turns)
- Same engine: Conductor, Ledger, Governor, ContextBuilder, EpisodicMemory

The rule: **zero engine edits to add the scenario**.  Only new files and a
two-line addition to `scenarios/__init__.py`.

## Consequences

- The engine/scenario boundary is verified, not assumed.
- Adding a third scenario (illustrated serial, blackboard swarm, etc.) has a
  demonstrated path: one new file, one registry line.
- The test `test_mystery_roots.py` acts as a regression guard for the contract.

## Result

Mystery Roots shipped with **zero engine edits**.  The modularity claim holds.
