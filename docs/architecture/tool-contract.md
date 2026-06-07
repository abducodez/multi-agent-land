# Tool Contract

Tools are the fourth stable contract.  An agent never holds a tool directly; it
asks the `ToolRegistry`, which enforces the agent's capability grant before
dispatching.  "The Artist gets image-gen, the Critic does not" is enforced by the
runtime, not by convention.

## Shape

A tool is a `(name, description, run)` triple.  `run(**params)` returns a
JSON-serialisable dict that the calling agent folds into its emitted event.

```python
registry = ToolRegistry()
registry.register("oracle", "Draw a cryptic omen. Params: {seed: str}.", oracle_fn)
```

## Capability enforcement

```python
registry.call(agent_name, manifest, tool, params)
#  raises CapabilityViolation  if tool not in manifest.tools
#  raises KeyError             if tool is granted but not registered
#  else -> tool's dict result
```

`ManifestAgent` wraps this as `self.call_tool(name, **params)` and injects the
descriptions of granted tools into the prompt (`AVAILABLE TOOLS` block).

## Worked example: the oracle path

`oracle-grove` pairs a tool-using agent with a tool-less one:

```
fortune-teller   handler: fortune-teller   tools: [oracle]   emits: oracle.spoke
scene-whisperer  (generic)                 tools: []         emits: world.observed
```

`FortuneTeller` (`src/agents/handlers.py`) calls the deterministic `oracle` tool,
weaves the omen into its prompt, and records the omen on the event payload — so
the tool output is first-class ledger data.  `scene-whisperer` has no grant, so a
call would raise `CapabilityViolation`.  `tests/test_tools.py` proves both.

## Why in-process now, MCP later

The `(name, description, run)` interface fronts an in-process callable today and
an MCP-server-backed tool tomorrow.  The MCP wiring (a stdio JSON-RPC client, a
tool broker advertising server capabilities, an image-gen server) is the next
step and is invisible to agents — they depend on the capability contract, not the
implementation.

```
Agent ──call_tool──► ToolRegistry ──► in-process fn        (today)
                                  └──► MCP client ─► server (next: image-gen, web-fetch)
```

## Code

- `src/tools/registry.py` — `ToolRegistry`, `ToolSpec`, `CapabilityViolation`
- `src/tools/builtins.py` — `oracle`, `default_tool_registry()`
- `src/agents/handlers.py` — `FortuneTeller` (handler that calls a tool)
- `config/agents/fortune-teller.yaml`, `config/scenarios/oracle-grove.yaml`
