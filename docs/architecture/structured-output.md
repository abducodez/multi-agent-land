# Structured JSON Output

## The problem with free prose

Small models drift when asked for free prose:
- They forget their role mid-response
- They output multiple sentences when one is needed
- The downstream router can't determine which event kind to assign
- Validation is hard — "did the model actually judge this, or just describe?"

The fix: **tell the model exactly what shape to output, every time**.

---

## The constraint block

Every agent prompt ends with an `OUTPUT FORMAT` block:

```
OUTPUT FORMAT
Reply with a single JSON object and nothing else — no prose before or after.
Schema: {"kind": "...", "text": "..."}
kind must be one of: world.observed | judge.verdict
text must be one or two sentences, vivid and specific.
Example: {"kind": "world.observed", "text": "A mossy ticket booth opens in a tree root."}
```

This is **not** OpenAI function-calling or tool use.
It works with any model on any inference endpoint — the constraint is in the prompt.

Function-calling is strictly better when available (enforced schema, no parsing).
Prompt-based JSON is the universal fallback that works everywhere.

---

## The parser

`parse_agent_output()` in `src/core/structured.py` implements a three-tier strategy:

### Tier 1: Direct JSON parse

```python
raw = '{"kind": "world.observed", "text": "The path folds itself into a paper crane."}'
# → clean parse, kind validation, return
```

### Tier 2: Extract embedded JSON

Some models prepend prose: `"Here is my response: {...}"`.
A regex extracts the first `{...}` block and attempts to parse it.

```python
raw = 'Certainly! Here is the JSON: {"kind": "agent.spoke", "text": "I collect echoes."}'
# → extracted and parsed
```

### Tier 3: Fallback wrap

If neither works, the raw text is wrapped in the fallback kind:

```python
raw = "The mushrooms charge admission to their bioluminescent shows."
# → {"kind": "agent.spoke", "text": "The mushrooms charge...", "_raw_fallback": True}
```

The `_raw_fallback` flag lets the system log how often the model isn't complying,
which is a signal that the prompt needs tuning or the model needs to be swapped.

---

## Kind validation

The parser enforces `may_emit` from the manifest:

```python
allowed = ["world.observed"]   # from manifest.may_emit
result = parse_agent_output(raw, allowed_kinds=allowed, fallback_kind="world.observed")
# if model emits "judge.verdict" → replaced with "world.observed"
```

This is the safety boundary: **an agent cannot emit an event kind it isn't authorised to emit**,
even if the model tries.  The critic cannot write to the scene; the scene-writer cannot judge.

---

## Extra payload fields

Agents can request additional fields by passing `extra_fields` to `json_instruction()`:

```python
json_instruction(
    allowed_kinds=["agent.spoke"],
    extra_fields=["emotion", "wants"]
)
# → schema includes "emotion" and "wants"
```

These fields are preserved in the event payload alongside `text` and `kind`.
They're useful for:
- Rendering emotional state in the UI
- Routing decisions (e.g. "if emotion=desperate, escalate to judge")
- Downstream agent context (the Echo agent could read the emitting agent's "wants")

---

## Testing structured output

Because the parser is a pure function, every compliance pattern is testable:

```python
# test_structured.py covers:
# - valid JSON parsed correctly
# - invalid kind replaced by fallback
# - JSON embedded in prose extracted
# - pure text wrapped in fallback kind
# - extra fields preserved
```

---

## Migration path to native function-calling

When the inference endpoint supports structured outputs:

1. Replace `ModelProvider.complete(role, prompt)` with `complete_structured(role, prompt, schema)`.
2. Pass the AgentOutput JSON schema as the response_format.
3. The parser becomes a no-op (model guarantees compliance).
4. `_raw_fallback` rate drops to 0%.

The rest of the system (manifest, conductor, ledger) does not change.
This is the value of keeping the constraint in the prompt abstraction layer,
not in the agent code directly.
