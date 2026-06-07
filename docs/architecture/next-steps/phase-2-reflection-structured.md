# Phase 2: Reflection Events + Structured Output

## Goal

Make agents coherent over long runs.  After 30+ turns without reflection, small
models forget their character and repeat themselves.  This phase wires two
mechanisms that solve it: **reflection** (memory compaction into beliefs) and
**structured JSON output** (reliable, parseable, character-consistent responses).

**Acceptance criteria**:
- Agents emit `agent.reflected` events when their `reflection_threshold` is hit.
- Every agent prompt includes a JSON constraint block and the parser handles all
  three compliance cases (clean JSON / embedded JSON / prose fallback).
- The `_raw_fallback` rate tracked in run stats drops below 10% with a real model.
- An agent running for 50 turns stays visibly in character (eval: human review of ledger).

---

## Implementation plan

### 2.1 Wire ReflectionTracker into ManifestAgent

`ReflectionTracker` is already implemented in `src/core/memory.py`.
`ManifestAgent` in `src/agents/base.py` needs to check it each turn:

```python
# In ManifestAgent.act():
threshold = self.manifest.memory.reflection_threshold
if threshold is not None:
    if not hasattr(self, "_reflection_tracker"):
        from src.core.memory import ReflectionTracker
        self._reflection_tracker = ReflectionTracker(self.manifest.name, threshold)
    if self._reflection_tracker.observe(recent_events):
        return self._emit_reflection(run_id, turn, projection, recent_events)
```

The reflection path calls the model with a special prompt asking for a one-sentence
belief synthesis, emits `agent.reflected` with payload `{"belief": "...", "based_on": [...]}`.

### 2.2 Reflection prompt

```python
def _emit_reflection(self, run_id, turn, projection, recent_events) -> Event:
    memory = EpisodicMemory(self.manifest.name, max_recent=20).format_for_prompt(recent_events)
    prompt = (
        f"IDENTITY\n{self.manifest.persona}\n\n"
        f"RECENT MEMORY (last 20 events you witnessed)\n{memory}\n\n"
        "TASK\n"
        "Synthesise the above into ONE high-level belief about yourself or the world. "
        "This belief will replace the raw memories in your future context.\n"
        'OUTPUT FORMAT\n{"kind": "agent.reflected", "text": "<one sentence belief>"}'
    )
    raw = self.model.complete(self.manifest.name + "-reflect", prompt)
    parsed = parse_agent_output(raw, ["agent.reflected"], "agent.reflected")
    return Event(run_id=run_id, turn=turn, kind="agent.reflected",
                 actor=self.manifest.name, payload=parsed)
```

### 2.3 Wire JSON instruction into ManifestAgent

`ManifestAgent.act()` already calls `json_instruction()` (Phase 2 infrastructure
is in `src/core/structured.py`).  The missing piece is passing `extra_fields`
from the manifest to support per-scenario payload shape.

Add to `AgentManifest`:
```python
output_extra_fields: list[str] = []
# e.g. ["emotion"] → agents emit {"kind": "...", "text": "...", "emotion": "..."}
```

### 2.4 Track _raw_fallback rate in run stats

Update `render_stats()` to count `_raw_fallback=True` events:
```python
fallback_count = sum(1 for e in events if e.payload.get("_raw_fallback"))
lines.append(f"  raw fallback rate: {fallback_count}/{len(events)}")
```

### 2.5 Update Thousand Token Wood agents to use ManifestAgent

Convert `SceneWhisperer`, `MischiefCritic`, `PocketActor`, `EchoAgent` from
extending `Agent` (Phase 1) to extending `ManifestAgent` (Phase 2):

```python
class SceneWhisperer(ManifestAgent):
    manifest = AgentManifest(
        name="scene-whisperer",
        role="worker",
        persona="...",
        subscribes_to=["run.started", "user.injected"],
        may_emit=["world.observed"],
        schedule=ScheduleConfig(tick_every=3),
        model_profile="fast",
        memory=MemoryConfig(window=6, reflection_threshold=20),
    )
```

This migration also moves the scenario from legacy scheduling to manifest-based routing.

---

## New event kind: `agent.reflected`

Add to `EventKind` in `src/core/events.py`:
```python
EventKind = Literal[
    "run.started",
    "world.observed",
    "agent.thought",
    "agent.spoke",
    "agent.reflected",    # ← new
    "judge.verdict",
    "user.injected",
]
```

Update `StageProjection.apply()` to render reflections:
```python
elif event.kind == "agent.reflected":
    self.agent_notes.append(f"💭 {event.actor} believes: {event.payload.get('text', '')}")
```

---

## Testing plan

| Test | File | What it verifies |
|---|---|---|
| `test_reflection_tracker_triggers` | `test_salience_memory.py` | Already passing |
| `test_manifest_agent_emits_reflection` | `test_manifest.py` | ManifestAgent emits reflected event at threshold |
| `test_reflected_event_globally_visible` | `test_memory.py` | EpisodicMemory includes agent.reflected for all agents |
| `test_fallback_rate_tracked` | `test_conductor.py` | run stats include fallback count |
| `test_structured_output_end_to_end` | new `test_integration.py` | Full step with real-model stub returns parseable JSON |

---

## Files to change

| File | Change |
|---|---|
| `src/core/events.py` | Add `agent.reflected` to EventKind |
| `src/core/projections.py` | Render `agent.reflected` events |
| `src/agents/base.py` | Wire ReflectionTracker into ManifestAgent.act() |
| `src/core/manifest.py` | Add `output_extra_fields` field |
| `src/agents/tiny_wood.py` | Convert agents to ManifestAgent |
| `src/ui/render.py` | Track and display _raw_fallback rate |
| `tests/test_manifest.py` | Tests for ManifestAgent reflection |

---

## Estimated effort: 1–2 days
