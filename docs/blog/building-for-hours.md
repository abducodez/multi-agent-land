# Building Agents That Run for Hours (Without Breaking the Bank)

*How the event-sourced architecture and the governor make long-running multi-agent scenarios tractable.*

---

## The problem with naive agent demos

Most agent demos die within 10 minutes.  Not because the idea is wrong — because
the implementation doesn't account for three things that happen over time:

1. **Small models drift**.  Without memory compaction and reflection, a villager
   forgets who they are after 20 turns and starts repeating scenes verbatim.

2. **Runaway cascades**.  Each agent fires, its output triggers another agent,
   that output triggers two more.  Without a governor, 3 agents quickly become
   30 inference calls per turn and a surprise bill.

3. **A crashed process loses everything**.  If your only state is in-memory
   and the process restarts, you're back to turn zero.

Here's how the Multi-Agent Land architecture solves all three.

---

## Memory that doesn't bloat

The key insight is that memory doesn't need its own store.
Agent memory is a **filtered view over the ledger** — always consistent,
always recoverable, and expressed as a pure function.

But a pure recency window eventually becomes useless: old important events
fall off while recent irrelevant events crowd in.  The solution is two-part:

### Salience scoring

Instead of "keep the last 8 events," rank every visible event by:

```
salience(e) = w_rel·relevance(e, current_scene)
            + w_rec·exp(−λ·turns_since(e))
            + w_imp·importance[e.kind]
```

User-injected events score highest (importance=0.95 + high relevance).
An old judge verdict that's directly relevant to the current scene scores higher
than a recent agent.spoke event that isn't.

### Reflection

Every N visible events, the agent synthesises a belief:

```
"The baker resents me because I ate her moonflower."
```

This one sentence replaces 10 raw events in the context window.
The belief is itself an event in the ledger (`agent.reflected`), so it's
visible to future turns — memory of memories, going arbitrarily far back
with constant context cost.

---

## The governor: cost before it bites you

Many small models running for hours is exactly the topology that produces
surprise bills.  The governor is the circuit breaker:

```python
Governor(
    max_turns=1000,          # scenario ends after N turns
    max_calls_per_turn=8,    # no single turn fires more than N model calls
    max_total_calls=5000,    # whole run cap
    hourly_budget_usd=2.0,   # cost cap (Phase 5)
)
```

The conductor checks the governor before every scheduled agent.
`BudgetExceeded` surfaces in the UI as a graceful end-of-run, not a hanging process.

The hibernation model: agents that aren't scheduled cost nothing.
20 villagers in the registry.  Only 3 acting per turn.
The other 17 are free — they're just manifest rows with memory in the ledger.

---

## Subscriptions: reacting vs. polling

The Phase 0 conductor scheduled agents by turn parity.  Simple but wrong at scale:
- What if an agent needs to react *immediately* when a visitor drops something?
- What if a judge should fire every time the scene changes, not every 3rd turn?

Subscription routing solves this:

```
user.injected → [echo, seedkeeper]  (react immediately)
world.observed → [mischief-critic]  (judge fires on every scene change)
beat.proposed → [artist, continuity-keeper, dialogue-writer]  (in parallel)
```

When an event is appended, subscribed agents are queued before the next tick.
The governor rate-limits the cascade so a busy board doesn't explode.
The conductor loop drain triggers before ticks, so reactive agents always act first.

---

## Crash recovery: the ledger is the checkpoint

Because all state derives from the append-only log:

```python
# After a crash:
ledger = SQLiteLedger.from_file("village.db")   # restore from disk
conductor = Conductor(scenario, ledger=ledger)
conductor.restore()                              # set turn counter from ledger
conductor.step()                                 # continue from where it stopped
```

The ledger is the checkpoint.  There is no separate checkpoint mechanism.
Every event that was committed before the crash is recovered automatically.
Events that were computed but not yet written are lost — but idempotency
(every event has a UUID) ensures that retrying those turns doesn't double-write.

### The snapshot strategy

For runs that could grow very large:
- Take a snapshot every 100 turns: `ledger.snapshot_to("snap-turn-100.db")`
- Keep the last 3 snapshots
- On crash: restore from the latest snapshot and replay the tail

Replay is fast because SQLite reads are sequential and the projection rebuild
is a pure in-memory fold over events.

---

## The wall-clock / sim-clock split

Two clocks. One problem.

**Sim-time** ticks once per conductor step.  It can run faster than real time
(batch mode) or slower (rate-limited for budget).

**Wall-clock** drives the production cadence.  "One episode per hour" is:
- A cron trigger every 60 minutes
- Which runs 60 sim-ticks
- Then publishes the episode artifact

```
Wall clock                           Sim clock
    │                                    │
    │   cron: 0 * * * *                  │
    │         │                          │
    └─────────┼──→ conductor.step(×60) ──┘
                              │
                          60 sim-ticks run at inference speed
                          (faster than real time in batch mode)
```

This separation is what makes the same engine work for:
- A demo that runs 5 turns per second under manual control
- A village that simulates a full day in 10 minutes
- A serial that publishes an episode every real hour for weeks

---

## Putting it together: the 24-hour village

```python
# scripts/run_village_day.py

ledger = SQLiteLedger("village.db")
conductor = Conductor(
    scenario=thousand_token_wood.build_scenario(),
    ledger=ledger,
    governor=Governor(max_calls_per_turn=6, hourly_budget_usd=2.0),
)
conductor.restore()  # resume from crash or start fresh

SIM_HOURS = 24
TICKS_PER_HOUR = 60

for hour in range(SIM_HOURS):
    for tick in range(TICKS_PER_HOUR):
        conductor.step()
    snapshot = f"snapshots/hour-{hour:02d}.db"
    ledger.snapshot_to(snapshot)
    print(f"Hour {hour:02d} complete: {len(ledger.events)} events committed")

print("Village day complete.")
```

No special framework.  No durable execution engine (unless you need the reliability).
The ledger is doing all the work.

---

## Next

- Phase 3: embed-based relevance in SalienceMemory (cosine similarity over `all-MiniLM-L6-v2`)
- Phase 4: MCP tool integration — the Artist calls an image-gen server mid-turn
- Phase 5: Modal deployment — 24-hour runs in the cloud with cron scheduling
- Phase 6: The Illustrated Serial — the scenario that needs all of the above
