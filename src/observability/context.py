"""Correlation context — run / turn / agent carried via :mod:`contextvars`.

The conductor binds a ``run_id`` for the whole episode and a ``turn`` each tick;
agents bind their ``name`` while acting. Logs and spans then pick these up
automatically (see :class:`~src.observability.logging_setup._ContextFilter` and
:func:`src.observability.span`), so a single LLM call line carries *which agent,
which turn, which run* without every call site threading the ids by hand.

``contextvars`` (not thread-locals) means the binding follows ``async`` tasks and
is isolated per Gradio request, so concurrent sessions never cross-contaminate.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("mal_run_id", default=None)
_turn: contextvars.ContextVar[int | None] = contextvars.ContextVar("mal_turn", default=None)
_agent: contextvars.ContextVar[str | None] = contextvars.ContextVar("mal_agent", default=None)

_VARS = {"run_id": _run_id, "turn": _turn, "agent": _agent}


def current_context() -> dict[str, object]:
    """The non-empty subset of {run_id, turn, agent} currently bound."""
    out: dict[str, object] = {}
    for key, var in _VARS.items():
        value = var.get()
        if value is not None:
            out[key] = value
    return out


def set_context(*, run_id: str | None = None, turn: int | None = None, agent: str | None = None) -> None:
    """Bind fields for the rest of the current context (no automatic reset).

    Use for long-lived scopes that don't nest cleanly — e.g. the conductor
    setting ``run_id`` in ``reset()`` and ``turn`` once per tick. For scoped
    binding that restores on exit, prefer :func:`bind`.
    """
    if run_id is not None:
        _run_id.set(run_id)
    if turn is not None:
        _turn.set(turn)
    if agent is not None:
        _agent.set(agent)


@contextmanager
def bind(run_id: str | None = None, turn: int | None = None, agent: str | None = None) -> Iterator[None]:
    """Scoped binding: set the given fields, restore the previous values on exit."""
    tokens: list[tuple[contextvars.ContextVar, contextvars.Token]] = []
    if run_id is not None:
        tokens.append((_run_id, _run_id.set(run_id)))
    if turn is not None:
        tokens.append((_turn, _turn.set(turn)))
    if agent is not None:
        tokens.append((_agent, _agent.set(agent)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
