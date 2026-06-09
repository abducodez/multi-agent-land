# Observability

A complete, modular log of the application — API/LLM calls, inference, memory, and
the core loop — readable in the terminal and live in the Gradio app. Built on
OpenTelemetry with a self-contained in-process backend. See ADR-0024.

## The facade

Every layer imports one thing:

```python
from src import observability as obs
```

| Call | Purpose |
|------|---------|
| `obs.configure(level=, fmt=, tracing=)` | Idempotent init (reads `MAL_*` env). Called by the app entrypoints; auto-runs on first use. |
| `obs.get_logger(__name__)` | A stdlib logger routed through the structured handlers. |
| `obs.log(event, level="info", **fields)` | One structured record: an `event` name + arbitrary fields (+ bound run/turn/agent). |
| `obs.span(name, **attrs)` | Context manager opening an OTEL span; nesting is automatic. |
| `obs.add_span_attrs(**attrs)` | Attach attributes to the active span. |
| `obs.incr(name, v=1, **labels)` / `obs.observe(name, v, **labels)` | Counter / histogram metric. |
| `obs.record_llm_call(model, prompt_tokens, completion_tokens, cost_usd)` | LLM-call counters. |
| `obs.record_agent_turn(agent, seconds)` | Agent-turn latency. |
| `obs.record_governor_trip(reason)` | Governor budget trip. |
| `obs.bind(run_id=, turn=, agent=)` / `obs.set_context(...)` | Correlation context (contextvars). |
| `obs.telemetry_store()` | In-memory store backing the Gradio Telemetry panel. |

## Span hierarchy

Spans nest by OTEL context — each layer opens only its own span:

```
run                         (conductor.reset)
└─ turn                     (conductor._tick / step_one)
   └─ agent.turn            (conductor._run_agent)
      ├─ memory.recall      (agents/base._recall → memory.py)
      │  └─ memory.index.search   (memory_index.py)
      ├─ llm.call | llm.structured  (models/litellm_provider.py)
      └─ tool.call          (tools/registry.py)
```

LLM spans use GenAI semantic-convention attributes (`gen_ai.system`,
`gen_ai.request.model`, `gen_ai.usage.input_tokens`/`output_tokens`) plus
engine-specific `llm.cost_usd`, `llm.prompt`, `llm.completion`, `llm.reasoning`.

## Configuration

| Env var | Default | Meaning |
|---------|---------|---------|
| `MAL_LOG_LEVEL` | `INFO` | Root level. `DEBUG` surfaces full prompts + memory. |
| `MAL_LOG_FORMAT` | `text` | Terminal format: `text` (human) or `json`. |
| `MAL_TRACING` | `memory` | Span sink: `off` \| `console` \| `memory` \| `both`. |
| `MAL_TELEMETRY_BUFFER` | `4000` | Ring-buffer size for logs/spans kept for the UI. |
| `MAL_TELEMETRY_TEXT_LIMIT` | `4000` | Prompt/memory truncation length in stored snapshots. |

## Conventions

- Import the facade only — never the OpenTelemetry SDK directly at a call site.
- Use the documented span names so the hierarchy stays consistent.
- **Never** log or attach API keys. Capture full prompts/memory at `DEBUG`; the
  store truncates them for the UI.
- The store is bounded and thread-safe; long sessions stay flat in memory.

## In-app panel

The Gradio "Telemetry" tab reads from `telemetry_store()`: a filterable log feed
(by agent/layer/level), metric charts (calls / tokens / cost / latency), and a
per-turn trace timeline where selecting a span reveals the prompt + memory the
agent saw.
