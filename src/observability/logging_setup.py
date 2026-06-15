"""Root logging configuration — structured records to stdout and to the store.

Provides a dependency-free JSON formatter for the whole engine, and adds:

  * a :class:`_ContextFilter` that stamps every record with the bound
    run/turn/agent (see :mod:`src.observability.context`); and
  * a :class:`_StoreHandler` that mirrors every record into the in-memory
    :class:`~src.observability.store.TelemetryStore` for the Gradio panel.

Two handlers are attached to the *root* logger — one terminal stream (text or
JSON) and one store handler — so any module's ``logging.getLogger(__name__)``
flows through both with no per-module wiring. Setup is idempotent: re-running it
removes the handlers it previously added (tagged ``_mal``) and never touches
handlers other libraries installed.
"""

from __future__ import annotations

import json
import logging
import sys

from .config import ObservabilitySettings
from .context import current_context
from .store import TelemetryStore

# LogRecord attributes that are either folded into a fixed key or dropped. Anything
# else on the record is a caller-supplied extra (event name, token counts, …).
_RESERVED: frozenset[str] = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def _payload(record: logging.LogRecord) -> dict[str, object]:
    """Flatten a record into a JSON-friendly dict (shared by formatter + store)."""
    data: dict[str, object] = {
        "ts": logging.Formatter().formatTime(record),
        "level": record.levelname,
        "logger": record.name,
        "event": getattr(record, "event", None) or record.getMessage(),
        "msg": record.getMessage(),
        "src": f"{record.module}:{record.lineno}",
    }
    if record.exc_info:
        data["exc"] = logging.Formatter().formatException(record.exc_info)
    for key, value in record.__dict__.items():
        if key in _RESERVED or key.startswith("_") or key in data:
            continue
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            value = repr(value)
        data[key] = value
    return data


class JsonFormatter(logging.Formatter):
    """One compact JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(_payload(record), ensure_ascii=False, default=repr)


class TextFormatter(logging.Formatter):
    """Human-readable: ``ts level logger [run/turn/agent] event — msg (extras)``."""

    def format(self, record: logging.LogRecord) -> str:
        ctx = current_context()
        tag = ""
        if ctx:
            tag = " [" + "/".join(str(ctx.get(k)) for k in ("run_id", "turn", "agent") if k in ctx) + "]"
        event = getattr(record, "event", None) or ""
        head = f"{self.formatTime(record)} {record.levelname:<5} {record.name}{tag}"
        body = f" {event}" if event else ""
        msg = record.getMessage()
        if msg and msg != event:
            body += f" — {msg}"
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _RESERVED and not k.startswith("_") and k not in ("event", "run_id", "turn", "agent")
        }
        if extras:
            body += " " + " ".join(f"{k}={v!r}" for k, v in extras.items())
        line = head + body
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


class _ContextFilter(logging.Filter):
    """Stamp the bound run/turn/agent onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in current_context().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


class _StoreHandler(logging.Handler):
    """Mirror each record into the in-memory store for the UI."""

    def __init__(self, store: TelemetryStore) -> None:
        super().__init__()
        self.store = store
        self._mal = True

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.store.add_log(_payload(record))
        except Exception:  # pragma: no cover - logging must never crash the app
            self.handleError(record)


def _configure_warnings() -> None:
    """Route Python warnings through logging.

    ``captureWarnings(True)`` funnels ``warnings.warn(...)`` into the ``py.warnings``
    logger, so deprecations surface in the CLI stream (``MAL_LOG_LEVEL``) and the
    Telemetry panel instead of being printed raw to stderr once and lost.

    We previously filtered out a pandas ``future.no_silent_downcasting`` warning from
    Gradio's queueing layer. That noise is now addressed at the source: pyproject caps
    ``pandas<3`` so Gradio stays on the pandas APIs it's written against, rather than us
    suppressing the symptom.
    """
    logging.captureWarnings(True)


def _install_starlette_status_compat() -> None:
    """Define Starlette's deprecated HTTP-status aliases as concrete module attributes.

    Gradio's routing layer still reads names like ``starlette.status.HTTP_422_UNPROCESSABLE_ENTITY``
    (renamed to ``..._CONTENT`` per RFC 9110). Starlette serves those old names through a
    module ``__getattr__`` that emits a ``DeprecationWarning`` on every access — once per
    request, since Gradio touches it inside ``queue_join_helper``. We can't pin our way out
    (Gradio requires ``starlette>=1.0.1`` and every 1.x carries the shim) and the real fix
    is upstream in Gradio.

    Rather than filter the warning (suppressing the symptom), we remove the *condition*:
    a module ``__getattr__`` only fires for names missing from the module namespace, so
    binding each deprecated alias to its concrete value makes the lookup resolve normally
    and the deprecation path never executes. Values come straight from Starlette's own
    ``__deprecated__`` table, so we change nothing but where the constant lives. Best-effort
    and version-tolerant: if Starlette drops the table, this no-ops.
    """
    try:
        from starlette import status
    except Exception:  # pragma: no cover - starlette always present via gradio, but stay safe
        return
    deprecated = getattr(status, "__deprecated__", None)
    if not isinstance(deprecated, dict):
        return
    for name, value in deprecated.items():
        if name not in vars(status):
            setattr(status, name, value)


def setup_logging(settings: ObservabilitySettings, store: TelemetryStore) -> None:
    """Attach the terminal + store handlers to the root logger (idempotent)."""
    _configure_warnings()
    _install_starlette_status_compat()
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_mal", False):
            root.removeHandler(handler)
    root.setLevel(settings.level)

    ctx_filter = _ContextFilter()

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(JsonFormatter() if settings.fmt == "json" else TextFormatter())
    stream.addFilter(ctx_filter)
    stream._mal = True  # type: ignore[attr-defined]
    root.addHandler(stream)

    store_handler = _StoreHandler(store)
    store_handler.addFilter(ctx_filter)
    root.addHandler(store_handler)
