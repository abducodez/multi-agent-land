"""Foundation smoke tests for the observability facade (ADR-0024).

Zero mocks: exercises the real facade with the in-memory store + a real OTEL
tracer provider, asserting that logs, spans, context, and metrics all land.
"""

from __future__ import annotations

import logging

from src import observability as obs


def _fresh(**env) -> None:
    """Reconfigure the facade with explicit settings (force past the singleton)."""
    obs.configure(level="DEBUG", tracing="memory", force=True, **env)
    obs.telemetry_store().clear()


def test_configure_is_idempotent():
    obs.configure()
    store = obs.telemetry_store()
    obs.configure()  # second call must not replace the store
    assert obs.telemetry_store() is store


def test_log_emits_structured_record_to_store():
    _fresh()
    obs.log("event.append", kind="agent.spoke", actor="spy-bex", turn=3)
    logs = obs.telemetry_store().recent_logs()
    appended = [r for r in logs if r.get("event") == "event.append"]
    assert appended, "expected the structured log to reach the store"
    assert appended[-1]["kind"] == "agent.spoke"
    assert appended[-1]["actor"] == "spy-bex"


def test_log_sanitises_reserved_field_names():
    _fresh()
    # 'module' is a reserved LogRecord attribute — must not raise, gets suffixed.
    obs.log("oddity", module="memory", name="x")
    record = [r for r in obs.telemetry_store().recent_logs() if r.get("event") == "oddity"][-1]
    assert record.get("module_") == "memory"
    assert record.get("name_") == "x"


def test_context_binding_stamps_logs():
    _fresh()
    with obs.bind(run_id="run-7", turn=2, agent="clue-gatherer"):
        obs.log("memory.recall", k=5)
    record = [r for r in obs.telemetry_store().recent_logs() if r.get("event") == "memory.recall"][-1]
    assert record["run_id"] == "run-7"
    assert record["turn"] == 2
    assert record["agent"] == "clue-gatherer"
    # Binding is scoped — it clears on exit.
    assert "run_id" not in obs.current_context()


def test_span_recorded_with_attributes_and_nesting():
    _fresh()
    with obs.bind(run_id="run-9"):
        with obs.span("turn", **{"mal.turn": 1}):
            with obs.span("llm.call", **{"gen_ai.request.model": "gpt-oss-20b"}):
                obs.add_span_attrs(**{"gen_ai.usage.output_tokens": 42})
    spans = {s.name: s for s in obs.telemetry_store().recent_spans()}
    assert "turn" in spans and "llm.call" in spans
    llm = spans["llm.call"]
    assert llm.attributes["gen_ai.request.model"] == "gpt-oss-20b"
    assert llm.attributes["gen_ai.usage.output_tokens"] == 42
    # llm.call nests under turn.
    assert llm.parent_id == spans["turn"].span_id


def test_span_records_exception_status():
    _fresh()
    try:
        with obs.span("boom"):
            raise ValueError("kaboom")
    except ValueError:
        pass
    boom = [s for s in obs.telemetry_store().recent_spans() if s.name == "boom"][-1]
    assert boom.status == "ERROR"


def test_metrics_counters_and_observations():
    _fresh()
    obs.record_llm_call("gpt-oss-20b", prompt_tokens=100, completion_tokens=20, cost_usd=0.001)
    obs.record_agent_turn("spy-bex", 0.25)
    obs.record_governor_trip("max_total_tokens")
    totals = obs.telemetry_store().counter_totals()
    assert totals["llm.calls"] == 1
    assert totals["llm.tokens.input"] == 100
    assert totals["llm.tokens.output"] == 20
    assert totals["governor.trips"] == 1
    latencies = obs.telemetry_store().metric_points("agent.turn.seconds")
    assert latencies and latencies[-1].value == 0.25


def test_store_is_bounded():
    obs.configure(force=True)
    store = obs.telemetry_store()
    store.clear()
    for i in range(store._logs.maxlen + 50):
        obs.log("spam", i=i)
    assert len(store.recent_logs(10_000_000)) == store._logs.maxlen


def test_get_logger_returns_standard_logger():
    assert isinstance(obs.get_logger("x.y"), logging.Logger)
