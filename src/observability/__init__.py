"""Observability facade — the one import every layer uses.

Usage at any call site::

    from src import observability as obs

    log = obs.get_logger(__name__)

    with obs.span("llm.call", **{"gen_ai.request.model": model}):
        ...
        obs.add_span_attrs(**{"gen_ai.usage.output_tokens": n})
        obs.record_llm_call(model, prompt_tokens=p, completion_tokens=n, cost_usd=c)

    obs.log("event.append", kind=event.kind, actor=event.actor)

This module owns the singletons (settings, in-memory store, tracer) and keeps the
public surface tiny and stable so instrumentation across the codebase never
touches the OpenTelemetry SDK directly. Structured logging + tracing + in-process
metrics all flow through here; the Gradio Telemetry panel reads from
:func:`telemetry_store`. See ADR-0024.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import replace
from typing import Iterator

from .config import ObservabilitySettings
from .context import bind, current_context, set_context
from .store import MetricPoint, SpanRecord, TelemetryStore, now_ts

__all__ = [
    "configure",
    "get_logger",
    "log",
    "span",
    "add_span_attrs",
    "incr",
    "observe",
    "record_llm_call",
    "record_agent_turn",
    "record_governor_trip",
    "bind",
    "set_context",
    "current_context",
    "telemetry_store",
    "settings",
    "ObservabilitySettings",
    "SpanRecord",
    "MetricPoint",
]

_configured = False
_settings: ObservabilitySettings | None = None
_store: TelemetryStore | None = None
_tracer = None

# Logging extras may not clobber these built-in LogRecord attributes.
_LOG_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
        "message",
    }
)


def configure(
    level: str | None = None,
    fmt: str | None = None,
    tracing: str | None = None,
    *,
    force: bool = False,
) -> None:
    """Initialise logging + tracing once (idempotent).

    Reads ``MAL_*`` env vars; explicit args override them. Safe to call from any
    entrypoint (app, Modal, a smoke script) — later calls are no-ops unless
    ``force=True``.
    """
    global _configured, _settings, _store, _tracer
    if _configured and not force:
        return

    resolved = ObservabilitySettings.from_env()
    overrides: dict[str, object] = {}
    if level is not None:
        overrides["level"] = level.upper()
    if fmt is not None:
        overrides["fmt"] = fmt.lower()
    if tracing is not None:
        overrides["tracing"] = tracing.lower()
    if overrides:
        resolved = replace(resolved, **overrides)

    _settings = resolved
    _store = TelemetryStore(capacity=resolved.store_capacity)

    from .logging_setup import setup_logging

    setup_logging(resolved, _store)

    from .tracing import init_tracing

    _tracer = init_tracing(resolved, _store)
    _configured = True


def _ensure() -> None:
    if not _configured:
        configure()


def settings() -> ObservabilitySettings:
    _ensure()
    assert _settings is not None
    return _settings


def telemetry_store() -> TelemetryStore:
    """The in-memory store backing the Gradio Telemetry panel."""
    _ensure()
    assert _store is not None
    return _store


# ── logging ─────────────────────────────────────────────────────────────────


def get_logger(name: str) -> logging.Logger:
    _ensure()
    return logging.getLogger(name)


def log(event: str, level: str = "info", *, logger: str = "mal", msg: str | None = None, **fields) -> None:
    """Emit one structured record: an ``event`` name plus arbitrary ``fields``.

    The bound run/turn/agent are added automatically. Reserved ``LogRecord``
    attribute names in ``fields`` are suffixed with ``_`` so logging never raises.
    """
    _ensure()
    lg = logging.getLogger(logger)
    lvl = logging._nameToLevel.get(level.upper(), logging.INFO)
    if not lg.isEnabledFor(lvl):
        return
    extra: dict[str, object] = {"event": event}
    for key, value in fields.items():
        extra[f"{key}_" if key in _LOG_RESERVED else key] = value
    lg.log(lvl, msg if msg is not None else event, extra=extra)


# ── tracing ───────────────────────────────────────────────────────────────--


def _attr_value(value: object) -> object:
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    import json

    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


@contextmanager
def span(name: str, **attributes) -> Iterator[object]:
    """Open a span named *name* with *attributes*; nesting is automatic.

    Yields the span (or ``None`` if tracing is off). Records and re-raises any
    exception with an ERROR status.
    """
    _ensure()
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name) as sp:
        for key, value in attributes.items():
            if value is not None:
                sp.set_attribute(key, _attr_value(value))
        try:
            yield sp
        except Exception as exc:  # noqa: BLE001 - record then re-raise
            from opentelemetry.trace import Status, StatusCode

            sp.record_exception(exc)
            sp.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def add_span_attrs(**attrs) -> None:
    """Attach attributes to the currently-active span (no-op if none/off)."""
    _ensure()
    if _tracer is None:
        return
    from opentelemetry import trace

    sp = trace.get_current_span()
    if sp is None or not sp.is_recording():
        return
    for key, value in attrs.items():
        if value is not None:
            sp.set_attribute(key, _attr_value(value))


# ── metrics (in-process, feeding the UI charts) ──────────────────────────────


def incr(name: str, value: float = 1, **labels) -> None:
    """Add to a cumulative counter (e.g. ``llm.calls``, ``governor.trips``)."""
    _ensure()
    assert _store is not None
    _store.add_metric(MetricPoint(name=name, value=float(value), ts=now_ts(), labels=labels), counter=True)


def observe(name: str, value: float, **labels) -> None:
    """Record a histogram-style observation (e.g. ``agent.turn.seconds``)."""
    _ensure()
    assert _store is not None
    _store.add_metric(MetricPoint(name=name, value=float(value), ts=now_ts(), labels=labels), counter=False)


def record_llm_call(model: str, prompt_tokens: int = 0, completion_tokens: int = 0, cost_usd: float = 0.0) -> None:
    """One LLM call's counters: call count, token totals, and spend."""
    incr("llm.calls", 1, model=model)
    incr("llm.tokens.input", prompt_tokens, model=model)
    incr("llm.tokens.output", completion_tokens, model=model)
    incr("llm.cost_usd", cost_usd, model=model)


def record_agent_turn(agent: str, seconds: float) -> None:
    """Latency of one agent's turn."""
    observe("agent.turn.seconds", seconds, agent=agent)


def record_governor_trip(reason: str) -> None:
    """A governor budget bound tripping, labelled by which one."""
    incr("governor.trips", 1, reason=reason)
