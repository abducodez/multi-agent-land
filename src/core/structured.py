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
        "Reply with a single JSON object and NOTHING else. No analysis, no reasoning, "
        "no <think> blocks, no markdown fences, no text before or after the JSON.\n"
        f'Schema: {{"{field_list}": "..."}}\n'
        f"kind must be one of: {kinds_str}\n"
        "text must be one or two sentences, vivid and specific — your line, never your reasoning.\n"
        "If you were given a secret word, never spell or quote it; describe it only.\n"
        "Example: "
        '{"kind": "' + allowed_kinds[0] + '", "text": "A brief, evocative response."}'
    )


# ── parser ────────────────────────────────────────────────────────────────────

# Reasoning models (and chat models told to "think") often wrap their scratchpad
# in tagged blocks or fence the JSON.  We strip those before parsing so a stray
# chain-of-thought never reaches the ledger as the spoken line — the leak we saw
# live, where "…I think the word is COFFEE…" was emitted as an agent's clue.
_REASONING_BLOCK = re.compile(
    r"<\s*(think|thinking|reason|reasoning|analysis|scratchpad|monologue)\s*>(.*?)<\s*/\s*\1\s*>",
    re.DOTALL | re.IGNORECASE,
)
_CODE_FENCE = re.compile(r"```[a-zA-Z]*\n?|\n?```")
# Sentences/lines that are obviously scratchpad or meta-commentary about the task —
# used only when salvaging a fallback line, so a "thinking out loud" model's notes
# ("But it must be one or two sentences…") don't become the spoken clue.
_SCRATCHPAD_LINE = re.compile(
    r"^\s*(we (?:need|must|should|are|have)|the (?:schema|text|clue|answer|response|user)|"
    r"thought\s*:|mood\s*:|json\s*:|output\b|let'?s|but\b|must\b|i (?:need|should|must|will|think|am|'ll)|"
    r"(?:one|two) sentences?|remember\b|note\b|so\b|okay\b|ok\b|first\b|now\b)",
    re.IGNORECASE,
)
# A quoted text value — from a partial JSON (``"text": "…"``) or a prose label (``Text: "…"``).
_TEXT_VALUE = re.compile(r'(?:"text"|text)\s*:\s*"([^"]{3,})"', re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _strip_reasoning(raw: str) -> str:
    """Remove tagged reasoning blocks and code fences from *raw*."""
    raw = _REASONING_BLOCK.sub(" ", raw or "")
    raw = _CODE_FENCE.sub("", raw)
    return raw.strip()


def extract_reasoning(raw: str, limit: int = 600) -> str:
    """Return inline ``<think>…</think>`` reasoning from *raw* (joined, trimmed).

    Empty when the model emitted no tagged reasoning — e.g. when vLLM already split
    it into ``reasoning_content`` (the provider captures that separately). Used to
    populate the mind-reader ``thought``; never fed back into any agent's prompt."""
    blocks = [m.group(2).strip() for m in _REASONING_BLOCK.finditer(raw or "")]
    joined = " ".join(b for b in blocks if b)
    return joined[:limit].strip()


def _balanced_objects(text: str) -> list[str]:
    """Return every top-level ``{...}`` substring in *text*, in order.

    A string-aware brace scan, so nested objects and braces inside string values
    don't truncate the match the way a flat ``\\{[^{}]+\\}`` regex would.
    """
    objects: list[str] = []
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                objects.append(text[start : i + 1])
                start = -1
    return objects


def parse_agent_output(
    raw: str,
    allowed_kinds: list[str],
    fallback_kind: str,
) -> dict[str, Any]:
    """Parse raw model output into a validated event payload dict.

    Strategy:
      1. Strip tagged reasoning blocks and code fences.
      2. Parse the LAST balanced ``{...}`` object — a reasoning model that emits
         scratchpad before its answer puts the real payload last.
      3. Fall back to *salvaging* a safe line (never the raw chain-of-thought).

    Returns a dict with at least ``{"kind": str, "text": str}``.  The caller
    constructs the Event from this dict.
    """
    cleaned = _strip_reasoning(raw)

    for candidate in reversed(_balanced_objects(cleaned)):
        result = _try_parse(candidate, allowed_kinds, fallback_kind)
        if result is not None:
            return result

    # No parseable object — salvage a clean line, never the scratchpad.
    return {"kind": fallback_kind, **_salvage_text(cleaned), "_raw_fallback": True}


def _salvage_text(cleaned: str) -> dict[str, str]:
    """Recover a safe spoken line from unparseable output.

    In order: the quoted value the model intended (closed ``"text": "…"`` /
    ``Text: "…"``); the tail after a lone opening quote (a clue the model began
    drafting before it was cut off); then the substantive sentences with
    scratchpad/meta dropped.  Only a neutral placeholder if nothing survives — so a
    "thinking out loud" monologue never becomes the spoken line.
    """
    m = _TEXT_VALUE.search(cleaned)
    if m:
        return {"text": m.group(1).strip()}
    # A lone (unterminated) opening quote — the model started drafting the clue.
    if cleaned.count('"') % 2 == 1:
        tail = cleaned.rsplit('"', 1)[-1].strip()
        if len(tail) >= 8 and not _SCRATCHPAD_LINE.match(tail):
            return {"text": tail[:280]}
    kept = [
        s.strip(" \"'")
        for s in _SENTENCE_SPLIT.split(cleaned)
        if len(s.strip()) >= 5 and not s.lstrip().startswith("{") and not _SCRATCHPAD_LINE.match(s.strip())
    ]
    if kept:
        return {"text": " ".join(kept)[:280]}
    return {"text": "…"}


# Meta-commentary / instruction-echo a weak model leaks when asked for JSON: drop any
# sentence matching this so the model's scratchpad — or the secret word it names while
# reasoning ("Secret word is COFFEE…") — never becomes the spoken line.
_META = re.compile(
    r"secret word|the word is|my word|need to|have to|also include|must (?:be|include|output|name|provide)|"
    r"\bjson\b|\bschema\b|\bmood\b|\bthought\b|one or two sentence|vivid and specific|"
    r"\bagent\.\w+|brief,? evocative|output format|\bfield\b",
    re.IGNORECASE,
)
_EXAMPLE_ECHO = "a brief, evocative response"


def clean_clue(raw: str) -> tuple[str, str]:
    """Extract a clean spoken line from PROSE output, plus the residue.

    Used on the live fallback when a model ignores the JSON schema and just talks
    (often a small or reasoning model).  Returns ``(clue, residue)``: *clue* is the
    spoken line with reasoning blocks and meta-commentary sentences stripped (``""``
    when nothing usable survives — the caller then skips the turn rather than ship
    junk); *residue* is the stripped thinking, usable as the private mind-reader
    thought (never shown to other agents)."""
    residue: list[str] = [m.group(2).strip() for m in _REASONING_BLOCK.finditer(raw or "")]
    cleaned = _strip_reasoning(raw)

    m = _TEXT_VALUE.search(cleaned)
    if m:
        return m.group(1).strip(), " ".join(p for p in residue if p)[:600].strip()

    if cleaned.count('"') % 2 == 1:
        tail = cleaned.rsplit('"', 1)[-1].strip()
        if len(tail) >= 8 and not _META.search(tail):
            cleaned = tail

    kept: list[str] = []
    for s in _SENTENCE_SPLIT.split(cleaned):
        sentence = s.strip()
        if len(sentence) < 6 or sentence.startswith("{"):
            continue
        (residue if _META.search(sentence) else kept).append(sentence.strip(" \"'"))

    return " ".join(kept)[:300].strip(), " ".join(p for p in residue if p)[:600].strip()


def is_usable_line(text: str) -> bool:
    """True when *text* is a real spoken line — not empty, a ``…`` placeholder, or the example."""
    normalized = (text or "").strip().lower().strip(" .…\"'")
    return len(normalized) >= 6 and normalized != _EXAMPLE_ECHO


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
