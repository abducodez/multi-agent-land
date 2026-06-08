# Agent Manifest Contract

The manifest is the third stable contract — the declarative description of one
agent.  Defined by `AgentManifest` (`src/core/manifest.py`), loaded from
`config/agents/<name>.yaml`, validated by `validate_agent()`.  Adding an agent is
dropping in a manifest; no engine edit.

## Schema

```yaml
name: scene-whisperer          # unique slug == actor on emitted events (required)
role: worker                   # worker | judge | observer | reflector
persona: >                     # IDENTITY block, injected verbatim every prompt (required)
  You are the Seedkeeper ... describe how the wood changed in one sentence.
handler: null                  # optional behaviour binding (see below)

# Communication contract
subscribes_to:                 # event kinds that trigger this agent (reactive)
  - user.injected
may_emit:                      # event kinds this agent is allowed to emit (authority)
  - world.observed
  - agent.reflected

# Scheduling
schedule:
  tick_every: 1                # also fire every N turns (null = event-driven only; 0 = every turn)
  max_consecutive: 3           # documented cap (enforcement deferred)

# Model (resolved to a concrete small model by the ModelRouter)
model_profile: fast            # tiny ≤4B | fast ≤7B | balanced ≤13B | strong ≤32B
model_endpoint: null           # optional: pin ONE specific catalogue model (modal/catalogue.py
                               # endpoint slug, e.g. minicpm-4-1-8b), overriding the tier above

# Memory (a view over the ledger, not separate state)
memory:
  window: 6                    # recent visible events in the prompt
  use_salience: false          # rank by relevance×recency×importance instead of pure recency
  salience_top_k: 8
  reflection_threshold: null   # emit agent.reflected every N visible events (null = off)

# Capability grants
tools: []                      # tool names the ToolRegistry will allow this agent to call

# Output shaping
output_extra_fields: []        # extra payload fields the model is asked for, e.g. ["emotion"]
```

## Field notes

- **`may_emit`** is the safety boundary.  An agent's structured output is coerced
  to one of these kinds; `agent.reflected` is permitted implicitly when reflection
  is enabled.
- **`subscribes_to` vs `schedule.tick_every`** are orthogonal — an agent may be
  reactive, periodic, or both.  Cadence is per-agent; scenarios don't schedule.
- **`model_profile`** never names a model; the router (config/env) does.  Mix
  tiers freely across a cast.
- **`model_endpoint`** is the escape hatch from tiers to a *specific* served model:
  a `modal/catalogue.py` endpoint slug the router resolves to that model's live
  binding (overriding `model_profile`).  `null` → route by tier.  This is how a cast
  pins concrete sponsor models — one mind on MiniCPM, the Judge on Nemotron — and what
  the Fishbowl Lab's per-cast model picker writes.  Offline it folds into the
  deterministic stub like any tier, so demos stay reproducible.  See ADR-0022.
- **`handler`** stays `null` for the common case (the generic `ManifestAgent`).
  Set it to a key registered via `@register_handler` for agents that call tools or
  need custom prompt logic; the YAML still supplies all declarative fields.
- **`memory.*`** layers are pure views over the ledger — see
  [memory-stack.md](../architecture/memory-stack.md).

See also: [manifest-spec.md](../architecture/manifest-spec.md) (detailed guide),
[scenario-config.md](scenario-config.md), [world-config.md](world-config.md).
