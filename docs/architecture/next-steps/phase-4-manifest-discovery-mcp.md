# Phase 4: Manifest Discovery + MCP Tool Integration

## Goal

True plugin architecture: drop in a new agent YAML + handler file and it appears
in the engine without any code edit.  Wire the first MCP tool (an image-generation
server) so agents can produce images, not just text.

**Acceptance criteria**:
- Adding a new agent requires only: (1) a YAML manifest file in `agents/<name>/`,
  (2) a Python handler file, (3) registration in scenario config.  Zero engine edits.
- At least one agent calls an MCP tool and the result appears in the event ledger.
- Capability-based access is enforced: the tool is not available to agents that
  don't declare it in `manifest.tools`.
- The modularity invariant test in CI passes: `test_add_agent_requires_no_engine_edits`.

---

## Implementation plan

### 4.1 YAML manifest files

Move from in-code `AgentManifest(...)` to YAML files that are loaded at startup:

```yaml
# agents/scene-whisperer/manifest.yaml
name: scene-whisperer
role: worker
persona: >
  You are the Seedkeeper of Thousand Token Wood — ancient, patient, delighted
  by small impossible things. Describe how the world changed in one sentence.
subscribes_to:
  - run.started
  - user.injected
may_emit:
  - world.observed
schedule:
  tick_every: 3
model_profile: fast
memory:
  window: 6
  reflection_threshold: 20
tools: []
```

Loader:
```python
# src/core/registry.py
from pathlib import Path
import yaml
from src.core.manifest import AgentManifest

def load_manifests(agents_dir: Path) -> dict[str, AgentManifest]:
    manifests = {}
    for manifest_file in agents_dir.rglob("manifest.yaml"):
        raw = yaml.safe_load(manifest_file.read_text())
        m = AgentManifest.model_validate(raw)
        manifests[m.name] = m
    return manifests
```

### 4.2 Handler discovery

Each agent directory contains a `handler.py` with a `build(model, manifest) -> Agent` function:

```python
# agents/scene-whisperer/handler.py
from src.agents.base import ManifestAgent
from src.core.manifest import AgentManifest
from src.models.provider import ModelProvider

def build(model: ModelProvider, manifest: AgentManifest) -> ManifestAgent:
    agent = ManifestAgent.__new__(ManifestAgent)
    agent.manifest = manifest
    agent.model = model
    return agent
```

The conductor discovers agents by scanning `agents/` for `(manifest.yaml, handler.py)` pairs.

### 4.3 Scenario config (replaces hardcoded agent lists)

```yaml
# scenarios/thousand-token-wood/config.yaml
name: thousand-token-wood
default_seed: "A village of stage props wakes up and argues about which fairy tale they belong to."
example_seeds:
  - "The last remaining compass has decided to point at feelings instead of north."
agents:
  - scene-whisperer
  - mischief-critic
  - pocket-actor
  - echo
```

The engine loads the scenario config, looks up each agent by name in the registry,
and builds the cast.

### 4.4 MCP tool integration

MCP (Model Context Protocol) is the open standard for exposing tools to models as servers.
Each tool is a standalone process; agents are clients.

**Architecture**:
```
Agent ──(MCP client)──→ Tool Registry ──→ MCP Server (image-gen)
                                      ──→ MCP Server (web-fetch)
                                      ──→ MCP Server (calculator)
```

**Step 1**: Implement a minimal MCP client in Python:

```python
# src/tools/mcp_client.py
import subprocess, json

class MCPClient:
    def __init__(self, server_cmd: list[str]) -> None:
        self._proc = subprocess.Popen(server_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    
    def call(self, tool: str, params: dict) -> dict:
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": tool, "arguments": params}}
        self._proc.stdin.write(json.dumps(request).encode() + b"\n")
        self._proc.stdin.flush()
        raw = self._proc.stdout.readline()
        return json.loads(raw)["result"]
```

**Step 2**: Implement an image-generation MCP server:

```python
# tools/image-gen/server.py
# MCP server that wraps an image-generation API (DALL-E, Stable Diffusion, etc.)
# Receives: {"prompt": str, "size": "512x512"}
# Returns:  {"url": str, "alt": str}
```

**Step 3**: Wire tools to agents via manifest.tools:

```python
# In ManifestAgent.act():
tools_context = ""
if self.manifest.tools and self._tool_registry:
    available = self._tool_registry.describe(self.manifest.tools)
    tools_context = f"\nAVAILABLE TOOLS\n{available}"
```

The tool registry is injected into the conductor and passed to agents.
An agent that calls a tool emits the result as part of its event payload.

### 4.5 Capability-based access control

The tool registry enforces `manifest.tools`:

```python
class ToolRegistry:
    def call(self, agent_name: str, manifest: AgentManifest, tool: str, params: dict) -> dict:
        if tool not in manifest.tools:
            raise CapabilityViolation(f"{agent_name} is not authorised to call {tool}")
        return self._clients[tool].call(tool, params)
```

---

## Modularity invariant test

```python
# tests/test_modularity.py
def test_adding_agent_requires_no_engine_changes(tmp_path):
    """Proof that the modularity claim holds: add an agent, zero engine edits."""
    # Write a new manifest and handler to tmp_path
    (tmp_path / "manifest.yaml").write_text(
        yaml.dump({"name": "newcomer", "persona": "A stranger.", ...})
    )
    (tmp_path / "handler.py").write_text(HANDLER_STUB)
    
    # Load manifests including the new one
    registry = load_manifests(tmp_path)
    assert "newcomer" in registry
    
    # Conductor can use it without any code change
    scenario = scenario_from_config(agents=["newcomer"], registry=registry)
    conductor = Conductor(scenario)
    conductor.reset("seed")
    conductor.step()
    # If we get here, the new agent ran without engine edits ✓
```

---

## Files to add

| File | Purpose |
|---|---|
| `src/core/registry.py` | Manifest loader + agent discovery |
| `src/tools/mcp_client.py` | Minimal MCP client |
| `src/tools/tool_registry.py` | Tool broker with capability enforcement |
| `tools/image-gen/server.py` | First MCP tool server |
| `agents/<name>/manifest.yaml` | Per-agent YAML manifests |
| `agents/<name>/handler.py` | Per-agent build() function |
| `scenarios/*/config.yaml` | Scenario config files |
| `tests/test_registry.py` | Manifest loading tests |
| `tests/test_modularity.py` | The invariant proof |

---

## Estimated effort: 3–4 days
