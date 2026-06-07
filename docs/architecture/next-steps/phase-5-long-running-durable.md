# Phase 5: Long-Running Scenarios + Durable Execution

> **Status: ◐ Partially realized.** Token/spend-aware governor, two-clock
> `step(n_ticks=N)`, ledger-as-checkpoint `restore()`, snapshots, and an optional
> Modal deployment (`modal_app.py`) shipped — see ADR-0013 and
> `docs/architecture/long-running.md`. Cron episode export, Temporal/Inngest
> wrappers, OpenTelemetry, and cost telemetry remain planned.

## Goal

Run a scenario for hours or days without manual intervention.  The system should:
- Self-pace its inference rate to avoid burning budget
- Survive process kills and resume from the ledger checkpoint
- Emit periodic "episode" snapshots suitable for sharing
- Support wall-clock cadence (e.g. "one episode per hour") alongside sim-time ticks

**Acceptance criteria**:
- A 24-hour village run completes with a coherent narrative and no human intervention
- Process kill at any point + relaunch resumes within 2 seconds of the last committed event
- A cron job triggers "publish episode" every hour and the result is a shareable artifact
- Total cost of a 24-hour run is tracked and bounded by the governor

---

## The two-clock model

```
Wall clock (real time)          Sim clock (scenario time)
      │                               │
      │ "one episode every hour"      │ "village advances by 1 hour every tick"
      │                               │
  Cron trigger ──────────────────→ conductor.step(n_ticks=60)
                                       │
                                   60 sim-ticks run at full inference speed
                                   (faster than real time if budget allows)
                                   (slower if governor throttles)
```

The conductor maps between clocks.  Sim-time is the domain unit; wall-time is the
production cadence.  This separation is what lets the village "run in realtime" for
a demo and "simulate a full day in 10 minutes" for testing.

### Implementing the wall-clock cadence

```python
# scripts/cron_episode.py — invoked by cron every hour
from src.core.sqlite_ledger import SQLiteLedger
from src.core.conductor import Conductor
from src.scenarios.thousand_token_wood import build_scenario

ledger = SQLiteLedger("runs/village.db")
conductor = Conductor(build_scenario(), ledger=ledger)
conductor.restore()

TICKS_PER_EPISODE = 60
for _ in range(TICKS_PER_EPISODE):
    conductor.step()

# Publish the episode
from scripts.export_episode import export
artifact = export(ledger, episode_number=conductor.turn // TICKS_PER_EPISODE)
print(f"Episode published: {artifact}")
```

---

## Durable execution integration (optional but powerful)

Hand-rolling crash recovery is manageable.  For high-reliability production runs,
wrap the conductor loop in a durable execution engine:

### Option A: Temporal

```python
# temporal_worker.py
from temporalio import workflow, activity

@workflow.defn
class ScenarioWorkflow:
    @workflow.run
    async def run(self, seed: str) -> None:
        await workflow.execute_activity(genesis_activity, seed)
        while True:
            await workflow.execute_activity(step_activity)
            await asyncio.sleep(workflow.now().timedelta_until(next_tick()))

@activity.defn
async def step_activity() -> None:
    conductor.step()   # state lives in the SQLiteLedger, not workflow state
```

Temporal handles: retries, timeouts, replays, durable timers.
The ledger handles: the domain state.
The workflow handles: the scheduling logic.

### Option B: Modal (cron + durable storage)

Modal is the simplest path if you're deploying to a hackathon/cloud environment:

```python
# modal_app.py
import modal

stub = modal.Stub("multi-agent-land")
volume = modal.Volume.from_name("ledger-volume", create_if_missing=True)

@stub.function(schedule=modal.Cron("0 * * * *"), volumes={"/data": volume})
def run_episode():
    ledger = SQLiteLedger("/data/village.db")
    conductor = Conductor(build_scenario(), ledger=ledger)
    conductor.restore()
    for _ in range(60):
        conductor.step()
    volume.commit()
```

Modal provides: serverless execution, persistent volumes, cron scheduling.
No ops.  Relevant for the **Modal Awards** prize track ($20k in credits).

### Option C: Inngest (event-driven durable functions)

```python
# inngest_fn.py
@inngest_client.create_function(
    fn_id="scenario-step",
    trigger=inngest.TriggerCron(cron="0 * * * *"),
)
async def run_episode(ctx: inngest.Context) -> None:
    conductor.step()
    # Inngest handles retries and state persistence
```

---

## Hibernation and budget management

Agents that aren't scheduled cost nothing — they're just rows in the registry.
The governor tracks spend per run and can pause the loop when the budget is hit:

```python
class Governor:
    hourly_budget_usd: float | None = None  # None = unlimited
    _spend_this_hour: float = 0.0

    def record_call(self, cost_usd: float = 0.0) -> None:
        self._spend_this_hour += cost_usd
        if self.hourly_budget_usd and self._spend_this_hour >= self.hourly_budget_usd:
            raise BudgetExceeded(f"Hourly spend cap ${self.hourly_budget_usd:.2f} hit")
```

LLM observability libraries (Langfuse, Helicone, OpenLLMetry) provide per-call cost
telemetry that feeds this tracker without instrumenting every model call manually.

---

## Episode export format

Each episode is a shareable artifact:

```python
@dataclass
class Episode:
    number: int
    run_id: str
    turn_start: int
    turn_end: int
    events: list[Event]
    scene_narrative: str        # observer's prose summary of the episode
    key_moments: list[Event]    # judge-promoted events and reflections
    seed: str

def export(ledger, episode_number, ticks_per_episode=60) -> Episode:
    start = episode_number * ticks_per_episode
    end = start + ticks_per_episode
    episode_events = [e for e in ledger.events if start <= e.turn < end]
    ...
```

Episodes can be rendered as:
- Markdown (blog post format)
- JSON (machine-readable, shareable)
- HTML comic page (with generated images if Artist is in the cast)

---

## Observability

Long-running agents need end-to-end tracing.  Implement with OpenTelemetry:

```python
from opentelemetry import trace

tracer = trace.get_tracer("multi-agent-land")

def step(self) -> None:
    with tracer.start_as_current_span("conductor.step") as span:
        span.set_attribute("turn", self.turn)
        span.set_attribute("scenario", self.scenario.name)
        for agent in scheduled:
            with tracer.start_as_current_span(f"agent.{agent.name}") as agent_span:
                event = agent.act(...)
                agent_span.set_attribute("event.kind", event.kind)
```

Traces give you: full turn-by-turn visibility, per-agent latency, model call debugging,
and the ability to trace a single event back through the conductor → context → model chain.

---

## Files to add/change

| File | Change |
|---|---|
| `src/core/governor.py` | Add hourly budget + cost tracking |
| `src/core/conductor.py` | Add restore(), snapshot_every, wall-clock tick count |
| `scripts/cron_episode.py` | Hourly episode trigger |
| `scripts/export_episode.py` | Episode export to JSON/Markdown |
| `scripts/resume_run.py` | Crash recovery entry point |
| `modal_app.py` (optional) | Modal deployment |
| `temporal_worker.py` (optional) | Temporal deployment |

---

## Estimated effort: 3–5 days (+ 1 day for chosen durable execution engine)
