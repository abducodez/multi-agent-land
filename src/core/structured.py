"""Structured output for agent responses — two layers, one schema.

Small models comply better under constraint.  Asking for free prose is
where they drift.  Asking for a specific JSON schema is where they stay
in character.

Two paths share the same ``{kind, text, …}`` shape:

  - **Live path (validated).**  ``build_output_model`` turns an agent's
    ``may_emit`` grant + ``output_extra_fields`` into a Pydantic model whose
    ``kind`` is constrained to the allowed kinds.  The live provider asks the
    model for *that* model and retries on validation failure, so the payload is
    valid by construction — no malformed prose ever reaches the ledger.
  - **Offline path (tolerant parse).**  ``json_instruction`` appends a JSON
    block to the prompt and ``parse_agent_output`` normalises whatever text the
    deterministic stub returns, wrapping non-compliant prose in the fallback
    kind.  This keeps demos and tests fully offline with no dependency.

Both paths are model/provider-agnostic: the live constraint rides on the same
``{kind, text, …}`` contract the parser produces, so downstream
(``Event`` construction, conductor, ledger) is identical either way.
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from pydantic import BaseModel


# ── output schema ─────────────────────────────────────────────────────────────

class AgentOutputError(ValueError):
    """Raised when output cannot be normalised to a valid event payload."""


# ── validated output model (live path) ─────────────────────────────────────────

def build_output_model(
    allowed_kinds: list[str],
    extra_fields: list[str] | None = None,
) -> type["BaseModel"]:
    """Build a Pydantic model for an agent's validated output.

    ``kind`` is constrained to *allowed_kinds* via a ``Literal``, so the model
    cannot emit a kind it is not authorised for; ``text`` plus any *extra_fields*
    are required strings.  Used on the live path with structured output: the
    provider retries on validation failure and returns a valid instance, which
    means the malformed-prose ``_raw_fallback`` path is never taken.

    Args:
        allowed_kinds: event kinds this agent may emit (the ``may_emit`` grant,
            reflection excluded).  Must be non-empty.
        extra_fields: optional additional payload fields (e.g. ``"emotion"``),
            each a required string alongside ``text``.
    """
    if not allowed_kinds:
        raise AgentOutputError("build_output_model requires at least one allowed kind")

    from pydantic import create_model

    # A single-element Literal is legal and still constrains to that one kind.
    kind_type = Literal[tuple(allowed_kinds)]  # type: ignore[valid-type]
    fields: dict[str, Any] = {
        "kind": (kind_type, ...),
        "text": (str, ...),
    }
    for name in extra_fields or []:
        fields[name] = (str, ...)

    return create_model(
        "AgentOutput",
        __doc__="Validated agent event payload (kind constrained to may_emit).",
        **fields,
    )


# ── prompt instruction ────────────────────────────────────────────────────────

def json_instruction(allowed_kinds: list[str], extra_fields: list[str] | None = None) -> str:
    """Return the JSON constraint block appended to every agent prompt.

    Args:
        allowed_kinds: event kinds this agent may emit.
        extra_fields: optional additional payload fields (e.g. "emotion", "wants").
    """
    field_list = '", "'.join(["kind", "text"] + (extra_fields or []))
    kinds_str = " | ".join(allowed_kinds)
    return (
        "\n\nOUTPUT FORMAT\n"
        "Reply with a single JSON object and nothing else — no prose before or after.\n"
        f'Schema: {{"{field_list}": "..."}}\n'
        f"kind must be one of: {kinds_str}\n"
        "text must be one or two sentences, vivid and specific.\n"
        "Example: "
        '{"kind": "' + allowed_kinds[0] + '", "text": "A brief, evocative response."}'
    )


# ── parser ────────────────────────────────────────────────────────────────────

def parse_agent_output(
    raw: str,
    allowed_kinds: list[str],
    fallback_kind: str,
) -> dict[str, Any]:
    """Parse raw model output into a validated event payload dict.

    Strategy:
      1. Try strict JSON parse.
      2. Try extracting the first {...} block from mixed prose+JSON output.
      3. Fall back to wrapping raw text in the fallback kind.

    Returns a dict with at least {"kind": str, "text": str}.
    The caller is responsible for constructing the Event from this dict.
    """
    raw = raw.strip()

    # --- attempt 1: direct JSON parse
    if raw.startswith("{"):
        result = _try_parse(raw, allowed_kinds, fallback_kind)
        if result is not None:
            return result

    # --- attempt 2: extract first {...} block (model added prose)
    match = re.search(r"\{[^{}]+\}", raw, re.DOTALL)
    if match:
        result = _try_parse(match.group(), allowed_kinds, fallback_kind)
        if result is not None:
            return result

    # --- fallback: wrap raw text
    return {"kind": fallback_kind, "text": raw[:512], "_raw_fallback": True}


def _try_parse(s: str, allowed_kinds: list[str], fallback_kind: str) -> dict[str, Any] | None:
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    # Normalise kind
    kind = data.get("kind", fallback_kind)
    if kind not in allowed_kinds:
        kind = fallback_kind
    data["kind"] = kind

    # Ensure text exists
    if "text" not in data or not isinstance(data.get("text"), str):
        data["text"] = str(data.get("content", data.get("message", s[:200])))

    return data
