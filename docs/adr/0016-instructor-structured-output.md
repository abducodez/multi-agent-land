# ADR-0016: Validated Structured Output on the Live Path

## Status

Accepted

## Context

Structured output (`src/core/structured.py`) constrains agents to a
`{kind, text, …}` event payload by appending a JSON `OUTPUT FORMAT` block to the
prompt and parsing the model's reply with `parse_agent_output`. The parser has
three tiers: strict JSON, a regex that extracts an embedded `{…}` block, and a
final **`_raw_fallback`** that wraps whatever prose the model returned as a valid
event under the agent's first allowed kind.

That last tier is a correctness hazard. The ledger is append-only and
event-sourced (ADR-0001): every appended event is permanent history that
projections and memory replay. When a small model drifts into prose, the
`_raw_fallback` path *silently* admits non-compliant text as a structured event —
corrupting the ledger with a payload the model never intended as that kind, and
masking the failure as a normal turn. The migration note in
`docs/architecture/structured-output.md` already anticipated replacing
prompt-and-parse with enforced structured output once the transport supported it.

The LiteLLM gateway (ADR-0015) made that transport available: it issues a single,
idiomatic `litellm.completion(...)` deliberately shaped so a layer could wrap it
with `instructor.from_litellm(litellm.completion)` for validated output.

## Decision

Add **validated structured output on the live path**, keeping the tolerant parser
as the **offline** path. The event schema and the agent's emitted-event contract
are unchanged (ADR-0009): this improves how a payload is *produced*, not what an
event *is*.

**Dynamic, constrained output model.** `build_output_model(allowed_kinds,
extra_fields)` (in `structured.py`) builds a Pydantic model whose `kind` is a
`Literal` over the agent's `may_emit` grant (reflection excluded) and whose `text`
plus any `output_extra_fields` are required strings. A model literally cannot
validate with a kind it is not authorised to emit — the same `may_emit` boundary
the parser enforced, now enforced by the type. It is pure Pydantic: no provider,
no network, independently testable, and importable with `instructor` not
installed.

**Structured capability on the gateway.** `LiteLLMProvider.complete_structured(
role, prompt, response_model)` wraps the *same* `litellm.completion` with
`instructor.from_litellm(...)` and calls `create_with_completion(...,
response_model=…, max_retries=…)`. Instructor re-prompts on validation failure;
on success it returns both the validated instance and the raw completion, so
tokens and cost are read from that completion exactly as `complete()` does
(`last_usage["cost_usd"]` / `last_cost`). The plain `complete()` is retained
unchanged. `instructor` is imported lazily inside the method.

**Capability-checked wiring.** `ManifestAgent.act()` delegates to
`_resolve_payload(...)`: if the routed provider exposes `complete_structured`
(`hasattr`), it builds the constrained model, calls it, and returns
`result.model_dump()` — a validated payload with **no `_raw_fallback`**. The stub
has no such method, so offline takes the existing `json_instruction` +
`parse_agent_output` path untouched. If a live structured call raises
(validation exhausted or transport error), the agent falls back to the parser
path so a turn still produces an event rather than dropping. Token/cost usage is
recorded from the provider in every branch, so the conductor's
`governor.record_call(...)` (ADR-0013, ADR-0015) is unaffected.

**Dependency.** `instructor` is a new optional `instructor` extra in
`pyproject.toml`. Lazy imports keep `import src.*` and `import app` working with
it not installed.

## Refinement: guided decoding, not tool calling (2026-06)

The first cut left Instructor on its default `Mode.TOOLS`, which encodes the
schema as an OpenAI **function/tool call**. That only validates on a served model
whose vLLM deployment has tool calling enabled with a *matching* parser. The
`fast` tier (`minicpm-4-1-8b`, ADR-0022 catalogue) has neither: MiniCPM4.1 emits a
custom `<|tool_call_start|> … <|tool_call_end|>` format for which vLLM 0.21.0 ships
no parser, so every structured call returned **`400 Bad Request`** (rejected at
request validation, ~40 ms, no generation) and degraded to the prose fallback —
turning the `fast` tier's fast validated-JSON path into a ~7 s prose round-trip
every turn, and feeding the `clean_clue` over-filter that dropped first-person
clues (the `spy-bex` "no usable line" failure).

`LiteLLMProvider.structured_mode` now defaults to **`json_schema`** — vLLM
**guided decoding** via `response_format`, which constrains output to the schema
*without* a tool-call parser, so it is correct for every served model regardless of
tool support (Gemma/Nemotron keep validating; MiniCPM now validates instead of
400ing). It is a per-provider field (an `instructor.Mode` member name): `json`
(plain `json_object` + schema-in-prompt) is the fallback if a backend rejects
`json_schema`, and `tools` restores the old behaviour for a model that prefers it.
No redeploy is needed — the change is entirely client-side on the request shape.

## Consequences

- On the live path, agent output is schema-valid and kind-constrained by
  construction; malformed prose is retried, not admitted. The `_raw_fallback`
  corruption cannot enter the ledger when structured output is active.
- The offline path is the default and unchanged: deterministic stub +
  `parse_agent_output`, including the `_raw_fallback` tier and its tests. The full
  suite passes with no network, no credentials, and neither `instructor` nor
  `litellm` installed.
- `build_output_model` is the single source of the output contract, shared by the
  constraint and (implicitly) the parser's `{kind, text}` shape, so the two paths
  stay aligned.
- The live structured call is two messages and one or more model round-trips
  (retries); cost is metered per the underlying completion. Retries are bounded by
  `max_retries` (default 2).
- A live structured failure degrades to the parser path. This preserves liveness
  but means a persistently failing structured call can still reach the
  `_raw_fallback` tier; the `_raw_fallback` flag remains the signal that a prompt
  or model needs attention.
- Follow-up: thread `max_retries` through the router's per-profile spec so a
  scenario can tune it per tier, and surface the structured-vs-parser path in the
  stats panel alongside the `_raw_fallback` rate.
