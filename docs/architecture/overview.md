# Architecture Overview

## Shape

The system is an event-sourced multi-agent runtime:

```text
Visitor -> Gradio UI -> Conductor -> Agent Runtime -> Event Ledger -> Projections -> Gradio UI
                                \-> Judge Agent ----/
```

The ledger is the only source of truth. Agent memory, world state, UI state, and bloggable traces are projections derived from events.

## Core Contracts

- Event envelope: immutable records with `run_id`, `turn`, `kind`, `actor`, `payload`, and `schema_version`.
- Agent interface: role-scoped actor that reads a projection and emits validated events.
- Scenario interface: declares seed, cast, genesis events, and scheduling policy.
- Model provider: binds agent roles to concrete small models without hardcoding providers into agent logic.

## Build Order

1. Walking skeleton in memory.
2. Persistent ledger.
3. Real small-model provider adapters.
4. Rich Gradio UI.
5. Memory retrieval and reflection.
6. Second scenario to prove modularity.
7. Durable workflow and deployment hardening.

## Design Pressure

The first submission should favor delight and demo reliability over platform completeness. Architecture exists to make the toy more surprising, observable, and extensible.

