# Configuration System

Configuration is **data, not code**.  Everything that makes a runnable world —
which agents exist, who participates, each agent's model tier and memory, the
scenario goal, tool grants, budgets — is a declarative document with a Pydantic
schema.  That single decision is what lets the same config be hand-edited,
emitted by a UI form, or proposed by an LLM, and then *verified before it runs*.

## The directory

```
config/
  models.yaml                 # logical profile -> concrete small model
  agents/<name>.yaml          # one AgentManifest per file
  scenarios/<name>.yaml       # one ScenarioConfig per file (cast = agent names)
```

Drop a file in, and it exists.  No engine edit, no import to add.

## The schemas (all validatable)

| Schema | File | What it describes |
|---|---|---|
| `AgentManifest` | `src/core/manifest.py` | one agent (persona, emits, model, memory, tools, handler) |
| `ScenarioConfig` | `src/core/config.py` | one scenario (goal, seed, cast, genesis, budget) |
| `ModelsConfig` | `src/core/config.py` | profile → concrete model bindings |
| `GovernorConfig` | `src/core/config.py` | call / token / spend budgets |
| `WorldConfig` | `src/core/config.py` | a whole world inline, cross-validated |

## The registry

`src/core/registry.py` loads the directory and assembles live objects:

```python
reg = default_registry()                       # loads config/
scenario = reg.build_scenario("mystery-roots") # cast names -> live agents
governor = reg.governor_for("mystery-roots")   # budget from YAML
router   = reg.build_router()                  # profiles from models.yaml
```

`build_scenario` resolves each `cast` entry against the agent registry, binds the
agent to the router and (optionally) a `ToolRegistry`, and returns a `Scenario`.

## "Configure from a prompt"

A UI form or an LLM emits a dict; one call validates it into a typed, cross-checked
object or a precise error:

```python
from src.core.config import validate_world, validate_agent, validate_scenario

world = validate_world({              # raises if a cast names an undefined agent
    "models":   {"offline": True},
    "agents":   [{"name": "town-crier", "persona": "...", "may_emit": ["crier.announced"]}],
    "scenarios":[{"name": "town-square", "default_seed": "...", "cast": ["town-crier"]}],
})
```

This is why the surface is safe to expose to non-engineers and to agents: the
schema is the guardrail.

## Behaviour vs. declaration

Most agents are pure declaration (a YAML manifest + the generic `ManifestAgent`).
An agent that needs custom behaviour (a tool call, special prompt logic) names a
`handler:` in its manifest; the registry instantiates the registered subclass via
`@register_handler`, but the YAML still supplies every declarative field.

## Proof

`tests/test_modularity.py` writes a brand-new agent + scenario as YAML into a temp
dir, loads them, and runs the conductor — asserting the new agent's (custom-kind)
events appear, with **zero engine edits**.
