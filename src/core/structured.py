"""Structured JSON output parsing for agent responses.

Small models comply better under constraint.  Asking for free prose is
where they drift.  Asking for a specific JSON schema is where they stay
in character.

Design:
  - ContextBuilder appends a JSON instruction block to every prompt.
  - The model emits JSON (or tries to).
  - parse_agent_output() validates and normalises the response.
  - Fallback: if the model doesn't comply, wrap the raw text in the
    schema so downstream systems always get a typed dict.

This is *not* OpenAI function-calling — it works with any model, any
provider, any inference endpoint, because the constraint is in the prompt.
"""
from __future__ import annotations

import json
import re
from typing import Any


# ── output schema ─────────────────────────────────────────────────────────────

class AgentOutputError(ValueError):
    """Raised when output cannot be normalised to a valid event payload."""


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
