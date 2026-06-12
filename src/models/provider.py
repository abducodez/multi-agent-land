from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from src import observability as obs


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for providers without usage data.

    Used by the deterministic stub and as a fallback when an endpoint does not
    return a usage block.  Good enough to feed the Governor's token budget.
    """
    return max(1, len(text) // 4)


# ── model-failure sentinel ──────────────────────────────────────────────────────
#
# ``complete()`` returns ``str`` by contract, so a failed call (a flaky connection, a
# 5xx, a bad key) can't surface as an exception here — it comes back wearing this prefix
# instead.  Agents detect it with :func:`is_model_error` and raise, so the conductor's
# resilient loop skips that turn and records it in ``agent_errors`` rather than speaking
# the raw error on stage (ADR-0023).
MODEL_ERROR_PREFIX = "[model error:"


def model_error(exc: object) -> str:
    """Format a failed model call as the recognizable failure sentinel."""
    return f"{MODEL_ERROR_PREFIX} {exc}]"


def is_model_error(text: str) -> bool:
    """True when *text* is the failure sentinel a provider returns instead of a line."""
    return (text or "").lstrip().startswith(MODEL_ERROR_PREFIX)


class ModelProvider:
    def complete(self, role: str, prompt: str) -> str:
        raise NotImplementedError

    @property
    def model_id(self) -> str:
        """The concrete model this provider runs — for per-event attribution.

        Uniform across backends: the live gateway sets ``self.model`` (e.g.
        ``openai/openbmb/MiniCPM4.1-8B``); the offline stub sets ``self.variant``
        (e.g. ``stub:fast``).  Empty string when neither is set.
        """
        return str(getattr(self, "model", None) or getattr(self, "variant", None) or "")

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
    # ── arena judges + competitors (ADR-0029) ──────────────────────────────────
    "mystery-judge": ("thinking", "truth", "calm"),
    "table-judge": ("calm", "thinking", "truth"),
    "debater-a": ("smug", "panic", "smug", "calm"),
    "debater-b": ("calm", "smug", "thinking", "smug"),
    "debate-judge": ("smug", "calm", "truth"),
    "storyteller-a": ("thinking", "calm", "smug", "truth"),
    "storyteller-b": ("calm", "thinking", "gossip", "truth"),
    "beat-judge": ("thinking", "calm", "truth"),
    "secret-keeper": ("smug", "calm", "gossip", "thinking"),
    "sprout-guesser": ("thinking", "thinking", "calm", "smug"),
    "sprout-judge": ("calm", "truth", "thinking"),
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
    "chat-curious": [
        "I think there's a better answer hiding just behind that one.",
        "If I keep asking, maybe we'll find the part nobody said out loud yet.",
        "I love this — I just want to know the why underneath the what.",
    ],
    "chat-skeptic": [
        "Sounds nice, but I've seen this go sideways before.",
        "Everyone's agreeing too fast; someone should poke the soft spot.",
        "I'll grant the point, but only after they've earned it.",
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
        with obs.span("llm.call", **{"gen_ai.system": "stub", "gen_ai.request.model": self.variant, "mal.role": role}):
            return self._complete(role, prompt)

    def _complete(self, role: str, prompt: str) -> str:
        digest = hashlib.sha256(f"{self.variant}:{role}:{prompt}".encode("utf-8")).hexdigest()
        choices = {
            "scene-whisperer": [
                "A mossy ticket booth opens in a tree root and sells yesterday's dreams for acorns.",
                "The path folds itself into a paper crane and refuses to point north.",
                "Every mushroom cap becomes a tiny stage light, waiting for a secret cue.",
            ],
            "mischief-critic": [
                "And so it is set down: the wood now keeps a booth that trades in yesterdays, and no traveller leaves without spending one.",
                "It has become real — the paths have stopped pointing north, and the wood answers to longing instead of direction.",
                "Let it be remembered: a ladder of echoes now rises toward the moon, and the wood is taller than it was at dawn.",
            ],
            "echo": [
                "What you dropped comes back wearing antlers of light, and the clearing leans in to listen.",
                "The wood swallows your word and returns it as a colour no one has named yet.",
                "Your gift is given back changed: smaller, warmer, and humming a tune from underground.",
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
            "chat-curious": [
                "Wait, what would actually change for the people who pass through every day?",
                "That's interesting — but who decides, and how do they know it's right?",
                "I'm curious: which one would the village still love in ten years?",
            ],
            "chat-skeptic": [
                "Sure, but a tree takes years and a bench takes an afternoon.",
                "Nice in theory; who waters it when everyone's gone home?",
                "I'm not convinced — comfort today might beat shade we never sit under.",
            ],
            "chat-host": [
                "Good points all around — let's hear what each of you would miss if we chose the other.",
                "Let me nudge us forward: what does the square need most, right now?",
                "Lovely tension here — say more about who this is really for.",
            ],
            # ── mystery roots / open table judges (ADR-0029) ────────────────────
            "mystery-judge": [
                "Verdict: the most likely truth is hypothesis-former's — the clue and the cause line up, and that ordering is what convinces me.",
                "Verdict: I endorse hypothesis-former's reading; it is the one explanation that leaves no clue stranded.",
                "Verdict: the evidence bends toward hypothesis-former's account — specific, testable, and unbroken by the doubt raised against it.",
            ],
            "table-judge": [
                "Verdict: chat-skeptic was the most persuasive voice — the point about who tends it after dark is the one I can't argue away.",
                "Verdict: I crown chat-curious; the question of what the village still loves in ten years reframed the whole table.",
                "Verdict: chat-skeptic takes it — turning comfort-today against shade-we-never-sit-under was the sharpest cut of the hour.",
            ],
            # ── debate duel (symmetric seats, different models) ─────────────────
            "debater-a": [
                "The bold path is always the right one — hesitation is just defeat in a slower coat.",
                "My opponent mistakes caution for wisdom; history rewards the daring, not the timid.",
                "Strip away the fear and what remains is obvious: we must act, and act now.",
            ],
            "debater-b": [
                "Every reckless 'yes' has a graveyard of consequences my opponent conveniently forgets.",
                "Restraint is not weakness — it is the only argument that survives the morning after.",
                "You call it boldness; I call it a beautifully worded mistake.",
            ],
            "debate-judge": [
                "Verdict: debater-a takes it — that line about history rewarding the daring landed clean and never wavered.",
                "Verdict: debater-b wins on the strength of 'the morning after,' the sharpest blow of the duel.",
                "Verdict: debater-a, by a hair — the closing call to act now outpunched every rebuttal.",
            ],
            # ── beat battle (symmetric seats, different models) ─────────────────
            "storyteller-a": [
                "The lighthouse keeper unfolds a wave that has signed its name in foam and three patient question marks.",
                "By dawn the gulls are reciting the sea's letters aloud, and one of them has started to weep with joy.",
                "A single drop climbs the spiral stair, knocks politely, and asks to borrow the lamp for a love note.",
            ],
            "storyteller-b": [
                "The tide leaves a sealed envelope of kelp on the top step, still warm from somewhere far below.",
                "Tonight the beam writes back in light, and the whole bay holds its breath to read the reply.",
                "The keeper discovers the sea has been practicing his own handwriting, only kinder, only braver.",
            ],
            "beat-judge": [
                "Verdict: storyteller-a wins — their beats turned a single wave into a character we ached for, surprising and warm in one breath.",
                "Verdict: storyteller-b takes it, every line opening a door the last one only hinted at, delight stacked on delight.",
                "Verdict: storyteller-a, by a whisper — the weeping gull was the kind of impossible detail that makes a tale sing.",
            ],
            # ── twenty sprouts (code-dealt secret word) ─────────────────────────
            "secret-keeper": [
                "Yes — you could hold it in one hand, if your hand were patient enough.",
                "No, it was never alive, though plenty of living things have leaned on it.",
                "Warmer now — it does belong to the wood, but not to any creature in it.",
            ],
            "sprout-guesser": [
                "Is the thing you're holding something a traveller would carry on the path?",
                "Does it make a sound, or is it silent until someone uses it?",
                "Then is it older than the trees, or younger than this morning's dew?",
            ],
            "sprout-judge": [
                "Verdict: the keeper kept its secret — the guesser circled close but never named the word.",
                "Verdict: a clean catch — the guesser cornered the word before the questions ran dry.",
                "Verdict: the grove falls quiet; the secret held, and the keeper smiles.",
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
                obj: dict[str, Any] = {
                    "kind": allowed_kinds[int(digest[2:4], 16) % len(allowed_kinds)],
                    "text": text,
                }
                for name in extra:
                    obj[name] = self._synth_field(name, role, digest)
                out = json.dumps(obj, ensure_ascii=False)

        prompt_tokens, completion_tokens = estimate_tokens(prompt), estimate_tokens(out)
        self._last_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        obs.add_span_attrs(
            **{
                "gen_ai.usage.input_tokens": prompt_tokens,
                "gen_ai.usage.output_tokens": completion_tokens,
                "llm.prompt": prompt,
                "llm.completion": out,
            }
        )
        obs.record_llm_call(
            self.variant, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, cost_usd=0.0
        )
        obs.log(
            "llm.call",
            role=role,
            model=self.variant,
            structured=False,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=0.0,
        )
        obs.log("llm.exchange", level="debug", role=role, model=self.variant, prompt=prompt, completion=out)
        return out

    def _synth_field(self, name: str, role: str, digest: str) -> Any:
        """Deterministically synthesise a value for one requested extra field."""
        if name == "mood":
            moods = _STUB_MOODS_BY_ROLE.get(role, _STUB_MOODS)
            return moods[int(digest[4:6], 16) % len(moods)]
        if name == "thought":
            opts = _STUB_THOUGHTS.get(role, _STUB_THOUGHT_DEFAULT)
            return opts[int(digest[6:8], 16) % len(opts)]
        # Well-known verdict fields (ADR-0029) get their real types, not a placeholder:
        # the stub names no winner (the field is optional, and a versus / judged handler
        # recovers the winner from the verdict text), and emits an empty score map — so
        # the offline path stays validation-clean and deterministic with no wasted re-ask.
        if name == "winner":
            return None
        if name == "scores":
            return {}
        # Unknown extra field: a short, stable placeholder keeps the output valid.
        return f"{name}:{digest[:4]}"
