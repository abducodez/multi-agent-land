# Turn Lifecycle

```text
1. Conductor checks the current turn and scenario schedule.
2. Scheduled agent receives a compact projection and recent event tail.
3. Agent calls a model provider or deterministic stub.
4. Agent emits one structured event.
5. Ledger appends the event idempotently.
6. Stage projection applies the event.
7. Judge emits a verdict on scheduled turns.
8. UI renders projection, ledger, and stats.
9. Journal tooling can summarize build progress from docs and event traces.
```

The model is treated as stateless. State lives outside the model and is passed in per turn.

