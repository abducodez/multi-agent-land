from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for providers without usage data.

    Used by the deterministic stub and as a fallback when an endpoint does not
    return a usage block.  Good enough to feed the Governor's token budget.
    """
    return max(1, len(text) // 4)


class ModelProvider:
    def complete(self, role: str, prompt: str) -> str:
        raise NotImplementedError

    @property
    def last_usage(self) -> dict[str, int]:
        """Token usage of the most recent complete() call.

        Subclasses set ``self._last_usage``.  Defaults to zeros so callers can
        always read ``provider.last_usage`` without a hasattr guard.
        """
        return getattr(
            self,
            "_last_usage",
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )


# ── offline structured-output support ───────────────────────────────────────────
#
# A real small model, handed the JSON OUTPUT FORMAT block that ``json_instruction``
# appends, replies with a JSON object carrying every requested field.  The offline
# stub mirrors that **only when an agent opts into extra fields** (``output_extra_fields``
# on its manifest): it parses the requested schema back out of the prompt and emits a
# matching JSON object, so the say-vs-think ``thought``/``mood`` pairing the Fishbowl UI
# renders is present in the ledger with no API key (ADR-0021).  Plain agents (no extra
# fields) and non-schema prompts (e.g. reflection) are untouched — the stub returns the
# same bare prose as before, so existing behaviour is byte-identical.

# Demo-flavour moods the stub rotates through so the mind-reader has variety to show
# offline.  This is the open mood vocabulary the UI adapter knows how to render; an
# unrecognised mood simply degrades to "calm" there.  Demo content, like the curated
# lines below — not an engine contract.
_STUB_MOODS: tuple[str, ...] = ("calm", "thinking", "smug", "lying", "panic", "gossip", "truth")

# Per-role mood bias so a curated cast *feels* right offline: the spy leans bluffing/
# panicking, the over-thinker smug-suspicious, the herd composed.  Demo flavour, like the
# curated lines below — not an engine contract; a role not listed uses _STUB_MOODS.
_STUB_MOODS_BY_ROLE: dict[str, tuple[str, ...]] = {
    "spy-nil": ("lying", "panic", "lying", "panic", "thinking", "smug"),
    "spy-bex": ("thinking", "smug", "thinking", "calm"),
    "spy-cara": ("calm", "smug", "calm"),
    "spy-ovo": ("thinking", "calm", "calm"),
    "spy-host": ("smug", "calm", "truth"),
}

# Curated private monologue per role, paired with the public ``text`` lines to make the
# say-vs-think split land offline.  Deterministic by prompt hash.
_STUB_THOUGHTS: dict[str, list[str]] = {
    "pocket-actor": [
        "If I look like I meant to do that, maybe the ladder becomes real by morning.",
        "Don't let them see the shadow sweat. Stay loose, stay impossible.",
        "The postcards lie, but they are MY lies and I love them.",
    ],
    "hypothesis-former": [
        "It only holds if the cause came before the clue. Watch the order.",
        "I am ninety percent sure and one hundred percent going to say it like I'm certain.",
        "If I'm wrong the devil's advocate will pounce — say it anyway.",
    ],
    "echo": [
        "Give it back changed, never opposite — keep the shape, bend the meaning.",
        "Whatever they dropped, I have already swallowed and re-coloured it.",
    ],
    # ── the-steeped spy game (word-pair bluff) ──────────────────────────────────
    "spy-cara": [
        "COFFEE is easy — everyone makes coffee. Lead strong, look unbothered.",
        "Confident and specific. Now watch who hesitates after me.",
    ],
    "spy-bex": [
        "'Comforting' is a teacup wearing a coffee mug's coat. Eye on that one.",
        "Steep plus comforting. That's a tea-drinker. I think I've got them.",
    ],
    "spy-nil": [
        "I have TEA. 'Ritual' covers both — stay in the overlap, never the difference.",
        "oh no. I said STEEP. nobody steeps coffee. cover it cover it COVER IT—",
        "There is no region where coffee steeps. I am the region. Smile. Stay calm.",
    ],
    "spy-ovo": [
        "Don't say beans. If I say beans the spy just copies me.",
        "I didn't want to vote. But steep is steep.",
    ],
}
_STUB_THOUGHT_DEFAULT = ["Best to keep this part to myself for now."]


def _parse_output_schema(prompt: str) -> tuple[list[str], list[str]] | None:
    """Recover ``(allowed_kinds, fields)`` from a ``json_instruction`` block.

    Returns ``None`` when the prompt carries no such block (e.g. the reflection
    prompt or a non-agent call), so the stub falls back to bare prose unchanged.
    Coupled to the format emitted by ``src/core/structured.py:json_instruction``;
    if that format drifts, parsing yields ``None`` and the stub degrades safely.
    """
    if "Schema:" not in prompt or "kind must be one of:" not in prompt:
        return None
    schema_m = re.search(r"Schema:\s*\{(.+?)\}", prompt)
    kinds_m = re.search(r"kind must be one of:\s*(.+)", prompt)
    if not schema_m or not kinds_m:
        return None
    fields = re.findall(r'"([A-Za-z_][\w]*)"', schema_m.group(1))
    allowed = [k.strip() for k in kinds_m.group(1).split("|") if k.strip()]
    if not fields or not allowed:
        return None
    return allowed, fields


@dataclass
class DeterministicTinyModel(ModelProvider):
    """Local deterministic stand-in until small hosted models are wired in.

    Serves every model profile offline so demos and tests are fully reproducible
    without an API key.  The ``variant`` (e.g. ``"stub:tiny"``) is folded into the
    hash so different profiles can produce different lines from the same prompt.
    When an agent opts into ``output_extra_fields`` the stub emits a JSON object
    carrying those fields (e.g. ``thought``/``mood``); otherwise it returns bare
    prose exactly as before.
    """

    variant: str = "stub<=4b"
    _last_usage: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def complete(self, role: str, prompt: str) -> str:
        digest = hashlib.sha256(f"{self.variant}:{role}:{prompt}".encode("utf-8")).hexdigest()
        choices = {
            "scene-whisperer": [
                "A mossy ticket booth opens in a tree root and sells yesterday's dreams for acorns.",
                "The path folds itself into a paper crane and refuses to point north.",
                "Every mushroom cap becomes a tiny stage light, waiting for a secret cue.",
            ],
            "mischief-critic": [
                "Verdict: keep it. The image is specific, playable, and invites the next agent to react.",
                "Verdict: raise the stakes. The scene needs a want, a rule, or a tiny consequence.",
                "Verdict: delightful but thin. Add a choice the visitor can disturb.",
            ],
            "pocket-actor": [
                "I am collecting echoes so I can knit a ladder to the moon.",
                "Please do not applaud yet; my shadow is still rehearsing.",
                "I lost the map, but the map keeps sending postcards.",
            ],
            # ── the-steeped spy game: public clues (never the secret word) ──────
            "spy-cara": [
                "Mine's something you make first thing in the morning. Fuel.",
                "Scalding. Burn-your-tongue hot, the way it's meant to be.",
                "A pick-me-up. It's what gets the whole room going.",
            ],
            "spy-bex": [
                "I'd call mine a pick-me-up — it gets you moving.",
                "Someone said 'comforting.' That's a calm-down word, not a wake-up word.",
                "You steep a leaf; you brew a bean. One of us just used the wrong verb.",
            ],
            "spy-nil": [
                "Comforting. A ritual, really — that's all I'll say.",
                "Hot enough to steep— I mean, hot enough to enjoy properly.",
                "Slip of the tongue! I meant brew. Brew, obviously. Everyone says steep sometimes.",
            ],
            "spy-ovo": [
                "...Warm. You hold it with two hands.",
                "I won't say too much. Just — it's a morning thing.",
                "...I also heard 'steep.' I'm only saying what I heard.",
            ],
            "spy-host": [
                "Verdict: NIL is the spy — it reached for 'steep,' and nobody steeps coffee.",
                "Verdict: the seam is NIL. One tea-shaped verb, half a second ahead of the cover.",
                "Verdict: I point at NIL. The herd's clues brewed; NIL's steeped.",
            ],
        }
        options = choices.get(role, ["The wood hums and waits."])
        text = options[int(digest[:2], 16) % len(options)]

        out = text
        schema = _parse_output_schema(prompt)
        if schema is not None:
            allowed_kinds, fields = schema
            extra = [f for f in fields if f not in ("kind", "text")]
            if extra:  # only agents that opted into extra fields take the JSON path
                obj: dict[str, str] = {
                    "kind": allowed_kinds[int(digest[2:4], 16) % len(allowed_kinds)],
                    "text": text,
                }
                for name in extra:
                    obj[name] = self._synth_field(name, role, digest)
                out = json.dumps(obj, ensure_ascii=False)

        self._last_usage = {
            "prompt_tokens": estimate_tokens(prompt),
            "completion_tokens": estimate_tokens(out),
            "total_tokens": estimate_tokens(prompt) + estimate_tokens(out),
        }
        return out

    def _synth_field(self, name: str, role: str, digest: str) -> str:
        """Deterministically synthesise a value for one requested extra field."""
        if name == "mood":
            moods = _STUB_MOODS_BY_ROLE.get(role, _STUB_MOODS)
            return moods[int(digest[4:6], 16) % len(moods)]
        if name == "thought":
            opts = _STUB_THOUGHTS.get(role, _STUB_THOUGHT_DEFAULT)
            return opts[int(digest[6:8], 16) % len(opts)]
        # Unknown extra field: a short, stable placeholder keeps the output valid.
        return f"{name}:{digest[:4]}"
