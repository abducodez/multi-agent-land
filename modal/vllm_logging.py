"""Structured (JSON) logging for the vLLM subprocess — stdlib only.

vLLM applies a standard :func:`logging.config.dictConfig` when the
``VLLM_LOGGING_CONFIG_PATH`` env var points at a JSON file (see vLLM's
``envs.py``). This module builds that config and ships the :class:`JsonFormatter`
it references, so one importable module serves both sides:

  * :func:`write_config` — called by ``service.serve()`` to drop the JSON config
    file into the container before launching ``vllm serve``; and
  * :class:`JsonFormatter` — imported *by name* from the JSON config when vLLM
    runs ``dictConfig`` in its own process.

For the second to work, this file is added to the container image and its
directory is placed on ``PYTHONPATH`` (see ``service.build_image``). Keeping it
**dependency-free** (no ``python-json-logger`` etc.) means there is no extra
wheel to install and no import path that can drift between versions — vLLM only
needs the stdlib plus this one file.

One JSON object is emitted per log line: ``ts``, ``level``, ``logger``, ``msg``,
the source ``module:lineno``, and any structured extras attached to the record
(vLLM threads request ids and token counts through these). Output stays on
stdout so Modal captures it like every other container log.
"""

from __future__ import annotations

import json
import logging

# Standard LogRecord attributes — everything here is either folded into a fixed
# JSON key below or deliberately dropped. Anything *else* on the record is a
# caller-supplied extra (e.g. a request id) and is included verbatim.
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


class JsonFormatter(logging.Formatter):
    """Render each log record as a single compact JSON line.

    Referenced from the dictConfig by dotted path (``vllm_logging.JsonFormatter``),
    so it must stay importable under that name in the container.
    """

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, object] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "src": f"{record.module}:{record.lineno}",
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        # Fold in any structured extras (request_id, token counts, ...). Values
        # that aren't JSON-serialisable fall back to repr so a stray object can
        # never crash the logging path.
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            data[key] = value
        return json.dumps(data, ensure_ascii=False, default=repr)


def build_config(level: str = "INFO") -> dict:
    """Return a ``logging.config.dictConfig`` that routes vLLM + uvicorn through
    :class:`JsonFormatter` on stdout at ``level``."""
    level = (level or "INFO").upper()
    handler = {
        "class": "logging.StreamHandler",
        "formatter": "json",
        "stream": "ext://sys.stdout",
    }
    logger = {"handlers": ["stdout"], "level": level, "propagate": False}
    return {
        "version": 1,
        # Keep vLLM's own loggers; we only swap their formatting/handler.
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": "vllm_logging.JsonFormatter"}},
        "handlers": {"stdout": handler},
        "loggers": {name: dict(logger) for name in ("vllm", "uvicorn", "uvicorn.access", "uvicorn.error")},
        "root": {"handlers": ["stdout"], "level": level},
    }


def write_config(path: str, level: str = "INFO") -> str:
    """Write the dictConfig JSON to ``path`` (for ``VLLM_LOGGING_CONFIG_PATH``)."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(build_config(level), fh)
    return path
