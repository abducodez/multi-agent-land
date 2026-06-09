"""OpenTelemetry tracing wired to the in-memory store (and optional console).

Builds a real :class:`~opentelemetry.sdk.trace.TracerProvider` so spans nest
automatically via OTEL context — the conductor opens ``run``/``turn``, the agent
opens ``agent.turn``, the provider opens ``llm.call``, and the parent/child links
fall out without anyone passing ids around.

Finished spans are captured by :class:`_StoreSpanProcessor` into the
:class:`~src.observability.store.TelemetryStore` (the Telemetry tab's trace view).
When ``tracing`` includes ``console`` a standard ``ConsoleSpanExporter`` also
prints them. OTEL is imported lazily inside :func:`init_tracing` so importing the
package stays cheap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .config import ObservabilitySettings
from .store import SpanRecord, TelemetryStore

if TYPE_CHECKING:  # pragma: no cover
    from opentelemetry.trace import Tracer

_SERVICE_NAME = "multi-agent-land"


def _make_store_processor(store: TelemetryStore, text_limit: int):
    from opentelemetry.sdk.trace import ReadableSpan
    from opentelemetry.sdk.trace.export import SpanProcessor

    def _coerce(value: object) -> object:
        if isinstance(value, str) and len(value) > text_limit:
            return value[:text_limit] + "…"
        return value

    class _StoreSpanProcessor(SpanProcessor):
        def on_start(self, span, parent_context=None) -> None:  # noqa: D401
            return None

        def on_end(self, span: "ReadableSpan") -> None:
            ctx = span.get_span_context()
            parent = span.parent
            start = span.start_time or 0
            end = span.end_time or start
            store.add_span(
                SpanRecord(
                    name=span.name,
                    trace_id=format(ctx.trace_id, "032x"),
                    span_id=format(ctx.span_id, "016x"),
                    parent_id=format(parent.span_id, "016x") if parent else None,
                    start_ms=start / 1e6,
                    end_ms=end / 1e6,
                    duration_ms=(end - start) / 1e6,
                    status=span.status.status_code.name if span.status else "UNSET",
                    attributes={k: _coerce(v) for k, v in dict(span.attributes or {}).items()},
                )
            )

        def shutdown(self) -> None:
            return None

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return True

    return _StoreSpanProcessor()


def init_tracing(settings: ObservabilitySettings, store: TelemetryStore) -> "Tracer | None":
    """Set up the global tracer provider per ``settings.tracing``; return a tracer.

    ``off`` leaves the API's default no-op provider in place (zero overhead).
    """
    if settings.tracing == "off":
        from opentelemetry import trace

        return trace.get_tracer(_SERVICE_NAME)

    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    # set_tracer_provider only takes effect once per process; if something already
    # installed a real provider, reuse it and just add our processors.
    existing = trace.get_tracer_provider()
    if isinstance(existing, TracerProvider):
        provider = existing
    else:
        provider = TracerProvider(resource=Resource.create({"service.name": _SERVICE_NAME}))
        trace.set_tracer_provider(provider)

    if settings.tracing in ("memory", "both"):
        provider.add_span_processor(_make_store_processor(store, settings.store_text_limit))
    if settings.tracing in ("console", "both"):
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    return trace.get_tracer(_SERVICE_NAME)
