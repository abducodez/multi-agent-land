"""Telemetry tab renderers — read the in-memory store, shape it for Gradio.

Pure functions over :func:`src.observability.telemetry_store`: a filterable log
feed, metric dataframes for the charts, and a per-trace timeline that surfaces the
prompt + memory each agent saw. No Gradio components are created here (the app
shell owns those, Unit 9 in ``app.py``); these just produce the markdown / HTML /
dataframes the components render. See ADR-0024.
"""

from __future__ import annotations

import html
from typing import Any

from src import observability as obs

#: Layer prefixes used by the feed's layer filter (matched against the ``logger``
#: name and the ``event`` namespace).
LAYERS = [
    "all",
    "llm",
    "agent",
    "memory",
    "ledger",
    "event",
    "projection",
    "tool",
    "governor",
    "router",
    "session",
    "modal",
    "run",
    "config",
    "manifest",
    "context",
]
LEVELS = ["all", "DEBUG", "INFO", "WARNING", "ERROR"]


def _short(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ── log feed ──────────────────────────────────────────────────────────────────


def log_rows(level: str = "all", layer: str = "all", limit: int = 250) -> list[list[str]]:
    """Recent structured logs as table rows: [time, level, agent/turn, event, detail]."""
    rows: list[list[str]] = []
    for rec in reversed(obs.telemetry_store().recent_logs(2000)):
        lvl = str(rec.get("level", ""))
        event = str(rec.get("event", ""))
        if level != "all" and lvl != level:
            continue
        if layer != "all" and not (event.startswith(layer) or str(rec.get("logger", "")).startswith(layer)):
            continue
        ctx = "/".join(str(rec[k]) for k in ("agent", "turn") if rec.get(k) is not None)
        if not ctx:
            continue
        # Detail = the most informative extra fields (skip the bookkeeping keys).
        skip = {"ts", "level", "logger", "event", "msg", "src", "run_id", "turn", "agent"}
        detail = " ".join(f"{k}={_short(v, 80)}" for k, v in rec.items() if k not in skip)
        ts = str(rec.get("ts", ""))[-12:]
        rows.append([ts, lvl, ctx, event, _short(detail, 200)])
        if len(rows) >= limit:
            break
    return rows


# ── metrics (chart data) ────────────────────────────────────────────────────


def kpi_markdown() -> str:
    """A one-line headline of the key counters."""
    c = obs.telemetry_store().counter_totals()
    calls = int(c.get("llm.calls", 0))
    tin, tout = int(c.get("llm.tokens.input", 0)), int(c.get("llm.tokens.output", 0))
    cost = c.get("llm.cost_usd", 0.0)
    tools = int(c.get("tool.calls", 0))
    trips = int(c.get("governor.trips", 0))
    events = int(c.get("ledger.events", 0))
    return (
        f"### Telemetry — live\n"
        f"**LLM calls** {calls} · **tokens** {tin:,} in / {tout:,} out · "
        f"**cost** ${cost:.4f} · **tool calls** {tools} · "
        f"**events** {events} · **governor trips** {trips}"
    )


def _df(rows: list[dict], columns: list[str]):
    """Build a pandas DataFrame (Gradio plots want one); empty-safe."""
    import pandas as pd

    return pd.DataFrame(rows or [{c: None for c in columns}][:0], columns=columns)


def calls_frame():
    """Counts by metric (calls / tool calls / events / trips) for a bar chart."""
    c = obs.telemetry_store().counter_totals()
    keep = {
        "llm.calls": "llm calls",
        "tool.calls": "tool calls",
        "ledger.events": "events",
        "governor.trips": "gov trips",
    }
    rows = [{"metric": label, "count": float(c.get(key, 0))} for key, label in keep.items()]
    return _df(rows, ["metric", "count"])


def tokens_frame():
    """Input vs output tokens for a bar chart."""
    c = obs.telemetry_store().counter_totals()
    rows = [
        {"kind": "input", "tokens": float(c.get("llm.tokens.input", 0))},
        {"kind": "output", "tokens": float(c.get("llm.tokens.output", 0))},
    ]
    return _df(rows, ["kind", "tokens"])


def latency_frame():
    """Agent-turn latency observations over time (seq index → seconds, by agent)."""
    points = obs.telemetry_store().metric_points("agent.turn.seconds")
    rows = [
        {"n": i, "seconds": round(p.value, 4), "agent": str(p.labels.get("agent", "?"))} for i, p in enumerate(points)
    ]
    return _df(rows, ["n", "seconds", "agent"])


# ── trace timeline ────────────────────────────────────────────────────────────

_PROMPT_KEYS = ("llm.prompt", "agent.prompt", "memory.query")
_OUTPUT_KEYS = ("llm.completion", "memory.visible_count")


def traces_html(limit_traces: int = 8) -> str:
    """Recent spans grouped by trace, rendered as an indented timeline.

    Each span shows name + duration + status; spans that carry a prompt or memory
    (``llm.prompt`` / ``agent`` / ``memory.*`` attributes) expand to reveal exactly
    what the agent sent and saw — the heart of the 'what did each agent do' view.
    """
    spans = obs.telemetry_store().recent_spans(3000)
    if not spans:
        return "<div class='tele-empty'>No traces yet — run the show to populate the timeline.</div>"

    by_trace: dict[str, list] = {}
    for sp in spans:
        by_trace.setdefault(sp.trace_id, []).append(sp)
    # Most recent traces first (by max end time within the trace).
    ordered = sorted(by_trace.items(), key=lambda kv: max(s.end_ms for s in kv[1]), reverse=True)

    out: list[str] = []
    for trace_id, group in ordered[:limit_traces]:
        depth = _depth_map(group)
        root_dur = max(s.end_ms for s in group) - min(s.start_ms for s in group)
        out.append(
            f"<div class='tele-trace'><div class='tele-trace-hd'>trace {trace_id[:8]} "
            f"· {len(group)} spans · {root_dur:.0f} ms</div>"
        )
        for sp in sorted(group, key=lambda s: s.start_ms):
            out.append(_span_html(sp, depth.get(sp.span_id, 0)))
        out.append("</div>")
    return "\n".join(out)


def _depth_map(group: list) -> dict[str, int]:
    by_id = {s.span_id: s for s in group}
    depth: dict[str, int] = {}

    def _d(span) -> int:
        if span.span_id in depth:
            return depth[span.span_id]
        parent = by_id.get(span.parent_id) if span.parent_id else None
        depth[span.span_id] = 0 if parent is None else _d(parent) + 1
        return depth[span.span_id]

    for s in group:
        _d(s)
    return depth


def _span_html(sp, depth: int) -> str:
    pad = 16 * depth
    status = "" if sp.status in ("UNSET", "OK") else f" <span class='tele-err'>{html.escape(sp.status)}</span>"
    attrs = sp.attributes or {}
    head = (
        f"<div class='tele-span' style='margin-left:{pad}px'>"
        f"<span class='tele-span-name'>{html.escape(sp.name)}</span> "
        f"<span class='tele-span-dur'>{sp.duration_ms:.1f} ms</span>{status}"
    )
    # Reveal the prompt / memory / model the span carried.
    detail_bits: list[str] = []
    model = attrs.get("gen_ai.request.model")
    if model:
        toks = f" · {attrs.get('gen_ai.usage.input_tokens', '?')}→{attrs.get('gen_ai.usage.output_tokens', '?')} tok"
        detail_bits.append(f"<div class='tele-kv'>model: {html.escape(str(model))}{toks}</div>")
    for key in _PROMPT_KEYS:
        if attrs.get(key):
            detail_bits.append(
                f"<details class='tele-det'><summary>{html.escape(key)}</summary>"
                f"<pre>{html.escape(str(attrs[key]))}</pre></details>"
            )
    if attrs.get("llm.completion"):
        detail_bits.append(
            f"<details class='tele-det'><summary>completion</summary>"
            f"<pre>{html.escape(str(attrs['llm.completion']))}</pre></details>"
        )
    body = "".join(detail_bits)
    return head + (f"<div class='tele-span-body'>{body}</div>" if body else "") + "</div>"


TELEMETRY_CSS = """
.tele-trace { border:1px solid #2e3d25; border-radius:8px; margin:8px 0; padding:8px; background:#0b0f0a; }
.tele-trace-hd { color:#5fd0d0; font-weight:700; margin-bottom:6px; }
.tele-span { padding:3px 0; border-top:1px dashed #1d2718; }
.tele-span-name { color:#8fe36a; font-weight:600; }
.tele-span-dur { color:#e3c14c; font-size:.85em; }
.tele-err { color:#e3786a; font-weight:700; }
.tele-det summary { color:#b59cff; cursor:pointer; font-size:.85em; }
.tele-det pre { white-space:pre-wrap; background:#070a06; color:#cfe8c0; padding:6px;
    border-radius:6px; max-height:240px; overflow:auto; font-size:.8em; }
.tele-kv { color:#cfe8c0; font-size:.82em; opacity:.85; }
.tele-empty { color:#8a9a7e; font-style:italic; padding:16px; }
"""
