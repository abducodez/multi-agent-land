# Agent Manifest Specification

The manifest is the **stable contract between the engine and any agent plugin**.
Nothing in the engine imports agent internals — it reads manifests.
Adding an agent is dropping in a manifest + a handler file; no engine edits.

---

## Schema

```python
class AgentManifest(BaseModel):
    # Identity
    name: str                        # unique slug, matches Agent.name
    role: AgentRole                  # worker | judge | observer | reflector
    persona: str                     # injected as IDENTITY in every prompt
    handler: str | None              # optional behaviour binding (registry handler key)

    # Communication contract
    subscribes_to: list[str]         # event kinds that trigger this agent
    may_emit: list[str]              # event kinds this agent may produce

    # Scheduling
    schedule: ScheduleConfig         # tick_every, max_consecutive

    # Model
    model_profile: ModelProfile      # tiny | fast | balanced | strong

    # Memory
    memory: MemoryConfig             # window, use_salience, salience_top_k, reflection_threshold

    # Capability grants
    tools: list[str]                 # tool names this agent may call (ToolRegistry)

    # Output shaping
    output_extra_fields: list[str]   # extra payload fields the model is asked for

    # Presentation metadata (optional; consumed by the UI presenter, ignored by the engine)
    hue: int | None                  # 0–360 stage colour; None → derived from name
    archetype: str | None            # short human label; None → derived from role
```

---

## Fields

### `name`
Unique slug.  Must match the `Agent.name` class attribute.
Used as the `actor` field in emitted events.

### `role`
Determines how the conductor treats this agent:
- `worker` — produces events; consumes world state
- `judge` — reads the full ledger digest; emits control events
- `observer` — read-only; renders to the UI; never emits
- `reflector` — special worker that compacts episodic memories

### `persona`
Fixed identity text injected as the `IDENTITY` block in every prompt.
Keep it tight — it occupies permanent prompt budget (never compressed).
Good persona anatomy: *who you are → what you notice → what you do → constraints*.

### `subscribes_to`
Event kinds that trigger this agent when they land in the ledger.
Exact kind strings only; no glob patterns.

```yaml
subscribes_to:
  - user.injected    # react to visitor disturbances
  - world.observed   # react to scene changes
```

Subscriptions and ticks are orthogonal — an agent may have both.

### `may_emit`
Event kinds this agent is permitted to produce.
The runtime validates every emitted event against this list.
This is the **safety boundary**: the Artist gets `image.generated`;
the Critic doesn't.

### `schedule.tick_every`
Also fire this agent every N turns regardless of subscriptions.
`None` = event-driven only.  `0` = every turn.

### `schedule.max_consecutive`
Maximum turns in a row this agent can act without a break.
Prevents any single agent from monopolising the loop.

### `model_profile`
Logical model tier.  Resolved to a concrete model name at runtime:

| Profile | Param target | Default fallback | Env override |
|---------|-------------|------------------|--------------|
| `tiny` | ≤4B | gpt-4o-mini | `MODEL_TINY` |
| `fast` | ≤7B | gpt-4o-mini | `MODEL_FAST` |
| `balanced` | ≤13B | gpt-4o-mini | `MODEL_BALANCED` |
| `strong` | ≤32B | gpt-4o | `MODEL_STRONG` |

The pattern: workers use `fast` or `tiny`; the judge and reflector use `balanced` or `strong`.

### `memory.window`
Number of recent visible events to include in every prompt (recency-window mode).
Default: 8.  Reduce to 4–5 for very small models.

### `memory.use_salience`
When `True`, rank visible events by salience score instead of pure recency.
Score: `w_rel·relevance + w_rec·recency + w_imp·importance`.
Keep `False` for first builds; enable when you see agents ignoring important old events.

### `memory.reflection_threshold`
Emit an `agent.reflected` event every N visible events.
`None` = reflection disabled.
Good first value: 20 (agents reflect roughly once per sim-day).

### `tools`
Tool names this agent may call.  The `ToolRegistry` allows only the listed tools
— capability-based least privilege (ADR-0012).  An agent that doesn't need
image-gen should not have it in `tools`.  The same contract fronts in-process
tools today and MCP servers later.

### `handler`
Optional behaviour binding.  `None` (the common case) → the generic
`ManifestAgent`.  When set, the registry instantiates the `ManifestAgent`
subclass registered under this key via `@register_handler` (for agents that call
tools or need custom prompt logic).  The YAML still supplies every declarative
field; the handler only adds behaviour.

### `output_extra_fields`
Additional payload fields the model is asked to emit beyond `{kind, text}`, e.g.
`["emotion"]` → `{"kind": "...", "text": "...", "emotion": "..."}`.  Lets a
scenario shape agent output without engine edits.  The Fishbowl cast uses
`["thought", "mood"]` to carry the say-vs-think pairing on `agent.spoke`; the
deterministic stub synthesises them offline so the mind-reader works with no API key
(ADR-0021).

### `hue` / `archetype`
Optional presentation metadata, consumed by the Fishbowl UI presenter and **ignored by
the engine** (ADR-0021).  `hue` (0–360) colours the agent's mind on stage; `archetype`
is a short human-readable label (e.g. "the over-thinker").  Both default to `None`, in
which case the presenter derives a stable hue from the name and an archetype from the
role — so existing manifests and tests are unaffected (backward-compatible additions only).

---

## Example: Thousand Token Wood cast

```python
SEEDKEEPER = AgentManifest(
    name="scene-whisperer",
    role="worker",
    persona=(
        "You are the Seedkeeper of Thousand Token Wood — ancient, patient, "
        "delighted by small impossible things. Describe how the world has changed "
        "in one specific sentence. Make it stranger or more alive."
    ),
    subscribes_to=["run.started", "user.injected"],
    may_emit=["world.observed"],
    schedule=ScheduleConfig(tick_every=3),
    model_profile="fast",
    memory=MemoryConfig(window=6),
)

JUDGE = AgentManifest(
    name="mischief-critic",
    role="judge",
    persona=(
        "You are the Mischief Critic — a sharp-eyed judge who demands specificity "
        "and playability. One-sentence verdict: what works, what would be stranger."
    ),
    subscribes_to=["world.observed"],
    may_emit=["judge.verdict"],
    schedule=ScheduleConfig(tick_every=None),  # event-driven only
    model_profile="balanced",
    memory=MemoryConfig(window=8, use_salience=True),
)
```

---

## Discovery and registration

This is now real (ADR-0011), not aspirational.  Manifests are YAML files under
`config/agents/<name>.yaml`.  `Registry.from_dir()` loads them, `ScenarioConfig.cast`
references them by name, and `Registry.build_scenario()` resolves the cast into
live agents bound to the `ModelRouter` and `ToolRegistry`.  The conductor inspects
`agent.manifest` to route subscriptions + ticks.

Adding an agent:
1. Drop `config/agents/<name>.yaml`.
2. Add `<name>` to a scenario's `cast`.
3. (Only if it needs custom behaviour) register a handler and set `handler:`.

No engine edit — proven by `tests/test_modularity.py`.
