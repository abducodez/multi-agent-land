"""In-memory telemetry store — the self-contained backend for the Gradio panel.

Logs, spans, and metric points are kept in bounded ring buffers (oldest dropped
once full) plus a cumulative counter table. This is deliberately *not* an
external collector: the whole monitoring story lives in-process so the live demo
shows logs, traces, and charts with nothing to deploy. The Telemetry tab reads
straight off the accessors here.

Thread-safe: Gradio serves requests on a pool, and OTEL ends spans from whatever
thread ran them, so every mutation takes the lock.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class SpanRecord:
    """A finished span, flattened for display and JSON-friendliness."""

    name: str
    trace_id: str
    span_id: str
    parent_id: str | None
    start_ms: float
    end_ms: float
    duration_ms: float
    status: str
    attributes: dict = field(default_factory=dict)


@dataclass
class MetricPoint:
    name: str
    value: float
    ts: float
    labels: dict = field(default_factory=dict)


class TelemetryStore:
    """Bounded, thread-safe buffers of recent logs / spans / metrics."""

    def __init__(self, capacity: int = 4000) -> None:
        self._lock = threading.Lock()
        self._logs: deque[dict] = deque(maxlen=capacity)
        self._spans: deque[SpanRecord] = deque(maxlen=capacity)
        self._metrics: deque[MetricPoint] = deque(maxlen=capacity * 4)
        self._counters: dict[tuple, float] = {}

    # ── ingest ──────────────────────────────────────────────────────────────

    def add_log(self, record: dict) -> None:
        with self._lock:
            self._logs.append(record)

    def add_span(self, span: SpanRecord) -> None:
        with self._lock:
            self._spans.append(span)

    def add_metric(self, point: MetricPoint, *, counter: bool = False) -> None:
        with self._lock:
            self._metrics.append(point)
            if counter:
                key = (point.name, tuple(sorted(point.labels.items())))
                self._counters[key] = self._counters.get(key, 0.0) + point.value

    # ── read (for the UI) ───────────────────────────────────────────────────

    def recent_logs(self, n: int = 200) -> list[dict]:
        with self._lock:
            return list(self._logs)[-n:]

    def recent_spans(self, n: int = 200) -> list[SpanRecord]:
        with self._lock:
            return list(self._spans)[-n:]

    def metric_points(self, name: str | None = None) -> list[MetricPoint]:
        with self._lock:
            points = list(self._metrics)
        return [p for p in points if name is None or p.name == name]

    def counter_totals(self) -> dict[str, float]:
        """Cumulative total per metric name, summed across label sets."""
        totals: dict[str, float] = {}
        with self._lock:
            for (name, _labels), value in self._counters.items():
                totals[name] = totals.get(name, 0.0) + value
        return totals

    def counters(self) -> dict[tuple, float]:
        """Raw cumulative counters keyed by (name, sorted-label-tuple)."""
        with self._lock:
            return dict(self._counters)

    def clear(self) -> None:
        with self._lock:
            self._logs.clear()
            self._spans.clear()
            self._metrics.clear()
            self._counters.clear()


def now_ts() -> float:
    """Wall-clock seconds — isolated here so callers don't import ``time``."""
    return time.time()
