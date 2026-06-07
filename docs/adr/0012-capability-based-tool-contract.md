# ADR-0012: Capability-Based Tool Contract

## Status

Accepted

## Context

The manifest carried a `tools` list, but nothing stood behind it — the fourth
stable contract (tools) was named, not implemented.  Agents need to call tools
(image generation, retrieval, oracles), and "the Artist gets image-gen, the
Critic does not" must be enforced by the runtime, not by convention.

## Decision

Add `ToolRegistry` (`src/tools/registry.py`) as a capability-checked broker.
Agents never hold a tool; they ask the registry, which checks the calling agent's
`manifest.tools` grant before dispatching, raising `CapabilityViolation` on a
denied call.  Tools are `(name, description, run)` triples; `run(**params)`
returns a JSON-serialisable dict the agent folds into its event.

`ManifestAgent` exposes `call_tool()` (capability-checked) and injects granted
tool descriptions into the prompt.  A built-in deterministic `oracle` tool plus a
`fortune-teller` handler agent exercise the whole path end-to-end (scenario
`oracle-grove`), with its result recorded on the ledger.

Live MCP servers are deliberately **deferred**: the same `(name, description,
run)` interface fronts an in-process callable today and an MCP-server-backed tool
later, so swapping one for the other is invisible to agents.

## Consequences

- Least-privilege tool access is enforced at runtime and is testable.
- Tool output is first-class ledger data, not a side channel.
- The contract is stable across in-process tools and future MCP servers.
- A tool-using agent and a tool-less agent can sit in the same cast, scoped
  independently — demonstrated in `oracle-grove`.
