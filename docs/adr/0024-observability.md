# ADR-0024: Observability — Structured Logging, Tracing, In-App Monitoring

## Status

Accepted

## Context

The engine had no cohesive observability. Each module made its own
`logging.getLogger(__name__)` with almost no calls (`conductor.py`, `memory.py`),
and the only structured output was the dependency-free JSON formatter for the
vLLM subprocess (`modal/vllm_logging.py`). There was no way to see, in one place,
**the prompts passed to each model and the memory each agent had access to** — the
two things that matter most when debugging a multi-agent run — nor any traces or
metrics across the api-call → inference → memory → core-loop path.

We want a *complete*, modular log of the application that foregrounds **agent
behaviour, data, and LLM/API calls**, readable both in the terminal and live in
the Gradio app, leveraging OpenTelemetry for logging, tracing, and basic
monitoring.

## Decision

Introduce a single `src/observability/` package — a thin facade over OpenTelemetry
plus an in-memory store — that every layer imports as `from src import
observability as obs`.

* **One stable facade.** `configure()`, `get_logger()`, `log(event, **fields)`,
  `span(name, **attrs)`, `add_span_attrs()`, `incr()/observe()` + named helpers
  (`record_llm_call`, `record_agent_turn`, `record_governor_trip`),
  `bind()/set_context()/current_context()`, and `telemetry_store()`. Call sites
  never touch the OTEL SDK directly, so instrumentation stays a one-liner and the
  wiring lives in one module.

* **Correlation by context, not by parameter.** Run / turn / agent are carried in
  `contextvars` and stamped onto every log record and span automatically. The
  conductor binds the run and turn; agents bind their name. A single `llm.call`
  line therefore says which agent, which turn, which run, with no threading.

* **Real OTEL tracing, self-contained backend.** A real `TracerProvider` makes
  spans nest by context: `run → turn → agent.turn → {memory.recall →
  memory.index.search} + {llm.call | llm.structured} + tool.call`. Finished spans
  and all log records flow into bounded in-memory ring buffers
  (`TelemetryStore`); a console exporter can also print spans. No Jaeger /
  Prometheus / Grafana to deploy — the whole monitoring story lives in-process and
  in the live demo. Metrics (LLM calls, tokens, cost, agent-turn latency, governor
  trips) are in-process counters/observations feeding the UI charts.

* **In-app surface.** A Gradio "Telemetry" tab reads from `telemetry_store()`: a
  filterable structured-log feed, metric charts, and a per-turn trace/timeline
  where selecting a span reveals the actual prompt + memory the agent saw.

* **Env-driven, no secrets.** `MAL_LOG_LEVEL` (DEBUG surfaces full prompts +
  memory), `MAL_LOG_FORMAT` (`text`|`json`), `MAL_TRACING`
  (`off`|`console`|`memory`|`both`, default `memory`). API keys are never logged
  or attached as span attributes; full prompts/memory are captured at DEBUG and
  truncated in stored snapshots for the UI.

OpenTelemetry (`opentelemetry-api`, `-sdk`, `-semantic-conventions`) is a
first-class core dependency. LLM spans use GenAI semantic-convention attributes
(`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.input_tokens` /
`output_tokens`) alongside engine-specific ones (`llm.cost_usd`, `llm.prompt`,
`llm.completion`, `llm.reasoning`).

## Consequences

* Any module gets full structured logging, tracing, and metrics by importing the
  facade — instrumentation is consistent and decoupled from the SDK.
* The live demo can show logs, traces, and charts with nothing extra to run; the
  in-memory buffers are bounded, so memory stays flat over a long session.
* OTEL is now a required dependency (the prior offline/no-new-deps stance is
  relaxed for observability, by decision); when `MAL_TRACING=off`, the API's
  no-op tracer keeps overhead negligible.
* `setup_logging` configures the **root** logger (the engine had none); it only
  manages handlers it tags `_mal`, leaving third-party handlers intact and making
  re-configuration idempotent.
* Future work (not in this ADR): optional OTLP export to an external collector,
  and mirroring the in-process metrics to an OTEL `MeterProvider`.
