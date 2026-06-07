# Agent Manifest Contract

The first code slice uses Python classes directly. The target plugin contract is:

```yaml
id: pocket-actor
display_name: Pocket Actor
subscribes:
  - world.observed
  - user.injected
emits:
  - agent.spoke
model_capability: tiny_creative
memory:
  working_events: 10
  reflections: false
tools: []
budget:
  max_turns_per_run: 100
  max_tokens_per_turn: 400
```

Manifests should become the boundary that lets new agents drop in without engine edits.

