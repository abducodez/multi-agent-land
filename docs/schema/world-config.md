# World Config Contract

`WorldConfig` (`src/core/config.py`) is a **whole runnable world as one
validatable artifact** — agents, scenarios, model profiles, and budgets inline.
It is the contract a UI form or an LLM emits when "configuring from a prompt," and
`validate_world()` checks it (including cross-references) before anything runs.

## Schema

```python
WorldConfig:
    models:    ModelsConfig        # profile -> concrete model bindings
    governor:  GovernorConfig      # default budgets
    agents:    list[AgentManifest] # agent definitions
    scenarios: list[ScenarioConfig]# scenarios referencing those agents by name
```

## Cross-validation

The key safety property: every scenario's `cast` must reference a defined agent.
A dangling reference is rejected at validation time, not discovered at run time.

```python
from src.core.config import validate_world

world = validate_world({
    "models":    {"offline": True},
    "governor":  {"max_turns": 500},
    "agents":    [{"name": "town-crier", "persona": "Announce the news.",
                   "may_emit": ["crier.announced"], "schedule": {"tick_every": 1}}],
    "scenarios": [{"name": "town-square", "default_seed": "Market day.",
                   "cast": ["town-crier"]}],
})
# -> WorldConfig

validate_world({
    "agents":    [{"name": "a", "persona": "p"}],
    "scenarios": [{"name": "s", "default_seed": "x", "cast": ["ghost"]}],
})
# -> ValidationError: scenario 's' references undefined agents: ['ghost']
```

## Granular validators

For incremental UI/agent workflows, validate one piece at a time:

| Function | Returns | Use |
|---|---|---|
| `validate_agent(dict)` | `AgentManifest` | a single proposed agent |
| `validate_scenario(dict)` | `ScenarioConfig` | a single proposed scenario |
| `validate_world(dict)` | `WorldConfig` | the whole world, cross-checked |

## Relationship to `config/`

The on-disk `config/` tree (one file per agent/scenario + `models.yaml`) is the
file-based projection of a `WorldConfig`.  `Registry.from_dir()` loads that tree;
`validate_world()` validates an equivalent inline document.  Same schemas, two
delivery surfaces — files for the repo, dicts for a UI or an LLM.

See also: [scenario-config.md](scenario-config.md), [agent-manifest.md](agent-manifest.md).
