"""Engine → Fishbowl design vocabulary.

Maps the engine's open event/profile vocabulary onto the prototype's presentation
language (``ui/raw/data.js``): the say/narrate/poke/verdict feed kinds, the fast/mid/deep
model tiers, the narrator voices, and the mood palette.  Everything degrades gracefully:
an unknown mood renders as ``calm``, an agent with no ``hue`` gets a stable colour from
its name, and a custom event kind with ``text`` still becomes a feed line.  Pure data
mapping — no Gradio, no engine mutation.
"""

from __future__ import annotations

import hashlib

from src.core.events import Event

# ── model tiers (the prototype's coloured tier dot) ─────────────────────────────
# Engine profiles (tiny/fast/balanced/strong) collapse onto the design's three tiers.
_PROFILE_TIER: dict[str, str] = {
    "tiny": "fast",
    "fast": "fast",
    "balanced": "mid",
    "strong": "deep",
}
TIER_COLOR: dict[str, str] = {"fast": "var(--lime)", "mid": "var(--cyan)", "deep": "var(--violet)"}

# ── moods (open vocabulary; unknown → calm) ─────────────────────────────────────
# label + CSS colour var, mirroring ui/raw/shared.jsx:MOOD_META.
MOOD_META: dict[str, tuple[str, str]] = {
    "thinking": ("thinking", "var(--ink-mid)"),
    "calm": ("composed", "var(--cyan)"),
    "lying": ("bluffing", "var(--coral)"),
    "panic": ("PANICKING", "var(--coral)"),
    "smug": ("smug", "var(--amber)"),
    "truth": ("sincere", "var(--lime)"),
    "gossip": ("scheming", "var(--amber)"),
}

# ── narrator voices (ui/raw/data.js:VOICES) ─────────────────────────────────────
VOICES: dict[str, tuple[str, str]] = {
    "doc": ("THE DOCUMENTARIAN", "deadpan nature host"),
    "noir": ("THE GUMSHOE", "noir detective"),
    "bard": ("THE BARD", "mythic storyteller"),
    "hype": ("THE PLAY-BY-PLAY", "breathless sportscaster"),
}
# A sensible default narrator per shipped scenario; the Lab may override it.
_SCENARIO_VOICE: dict[str, str] = {
    "thousand-token-wood": "bard",
    "the-steeped": "doc",
    "mystery-roots": "noir",
    "oracle-grove": "doc",
}


# ── agent identity ──────────────────────────────────────────────────────────────


def agent_hue(manifest) -> int:
    """The manifest's ``hue``, or a stable 0–360 hue derived from the name."""
    hue = getattr(manifest, "hue", None)
    if hue is not None:
        return int(hue) % 360
    digest = hashlib.sha256(manifest.name.encode("utf-8")).hexdigest()
    return int(digest[:4], 16) % 360


def agent_archetype(manifest) -> str:
    """The manifest's ``archetype``, or a fallback derived from its role."""
    return getattr(manifest, "archetype", None) or f"the {manifest.role}"


def model_tier(profile: str) -> str:
    return _PROFILE_TIER.get(profile, "mid")


def _endpoint_entry(endpoint: str) -> dict | None:
    """The Modal catalogue entry for a casting key, or None (offline/unknown-safe)."""
    from src.models import modal_catalogue

    try:
        return modal_catalogue.entry_by_key(endpoint)
    except Exception:  # pragma: no cover - catalogue is import-safe, belt-and-suspenders
        return None


def model_label(endpoint: str) -> str:
    """A short, human-readable model name for a Modal catalogue casting key.

    Resolves the key to its ``served_model_id`` and shows just the model name (the part
    after the ``org/`` prefix), e.g. ``minicpm-4-1-8b`` → ``MiniCPM4-8B``.  An unknown key
    (or an unavailable catalogue) degrades to the raw key so the card is never blank.
    """
    entry = _endpoint_entry(endpoint)
    if entry is None:
        return endpoint
    served = str(entry.get("served_model_id") or "")
    short = served.rsplit("/", 1)[-1] if served else ""
    return short or endpoint


def short_model_name(model_id: str) -> str:
    """Prettify a *concrete* resolved model string for the card's model badge.

    Unlike :func:`model_label` (which resolves a catalogue *key*), this takes the
    model that actually ran, recorded on the event envelope (ADR-0028): a served id
    like ``openai/openbmb/MiniCPM4.1-8B`` → ``MiniCPM4.1-8B``, or the offline
    ``stub:fast`` left as-is.  Empty input degrades to ``""`` so callers fall back.
    """
    text = (model_id or "").strip()
    if not text or text.startswith("stub:"):
        return text
    return text.rsplit("/", 1)[-1] or text


def agent_model(manifest) -> str:
    """The model an agent is actually running, for the Show's model badge.

    Honours the ADR-0022 ``model_endpoint`` override — the concrete catalogue model the
    cast member is bound to — and falls back to the ``model_profile`` tier name when the
    agent routes purely by profile.
    """
    endpoint = getattr(manifest, "model_endpoint", None)
    if endpoint:
        return model_label(str(endpoint))
    return manifest.model_profile


def agent_tier(manifest) -> str:
    """The tier dot colour key for an agent, following its real model.

    When the agent overrides its profile with a catalogue endpoint, the dot reflects that
    model's own profile; otherwise it reflects the declared ``model_profile``.
    """
    endpoint = getattr(manifest, "model_endpoint", None)
    if endpoint:
        entry = _endpoint_entry(str(endpoint))
        if entry and entry.get("profile"):
            return model_tier(str(entry["profile"]))
    return model_tier(manifest.model_profile)


# ── moods + voices ──────────────────────────────────────────────────────────────


def normalize_mood(mood: str | None) -> str:
    return mood if mood in MOOD_META else "calm"


def mood_label(mood: str | None) -> str:
    return MOOD_META.get(normalize_mood(mood))[0]


def mood_color(mood: str | None) -> str:
    return MOOD_META.get(normalize_mood(mood))[1]


def scenario_voice(scenario_name: str) -> str:
    return _SCENARIO_VOICE.get(scenario_name, "doc")


# ── live / offline pill (the meters' run-mode indicator) ─────────────────────────
# Lime + filled bullet when bound to a live inference backend; dim + hollow when the
# deterministic offline stub is driving the show.
def live_pill(offline: bool) -> tuple[str, str]:
    """``(label, css_color)`` for the meters' LIVE/OFFLINE pill."""
    if offline:
        return ("○ OFFLINE · STUB", "var(--ink-mid)")
    return ("● LIVE", "var(--lime)")


# ── feed vocabulary (say / narrate / poke / verdict) ────────────────────────────


def event_to_feed_item(event: Event, cast_names: list[str] | None = None) -> dict | None:
    """Map one engine event to a Fishbowl feed item, or ``None`` to omit it."""
    kind = event.kind
    p = event.payload
    if kind == "world.observed":
        # A cast member who narrates the wood (e.g. the seedkeeper scene-whisperer, whose
        # `may_emit` is world.observed) is a *speaker*, not the anonymous house narrator:
        # credit the line to that agent so it reads as "scene-whisperer …" and lights its
        # MindCard. Genesis / world lines — emitted by the scenario itself, not a cast
        # member — keep the narrator voice (THE BARD) below.
        if cast_names and event.actor in cast_names:
            return {
                "kind": "say",
                "agent": event.actor,
                "said": p.get("text", ""),
                "thought": p.get("thought"),
                "mood": normalize_mood(p.get("mood")),
            }
        return {"kind": "narrate", "voice": p.get("voice"), "text": p.get("text", "")}
    if kind == "user.injected":
        return {"kind": "poke", "label": p.get("label", "DISTURBANCE"), "text": p.get("text", "")}
    if kind == "judge.verdict":
        return {"kind": "verdict", "text": p.get("text", ""), "reveal": p.get("reveal", []), "agent": event.actor}
    if kind in ("run.started", "agent.reflected"):
        return None
    if kind == "agent.thought":
        return {
            "kind": "say",
            "agent": event.actor,
            "said": None,
            "thought": p.get("text"),
            "mood": normalize_mood(p.get("mood")),
        }
    if kind in ("agent.spoke", "oracle.spoke") or "text" in p:
        return {
            "kind": "say",
            "agent": event.actor,
            "said": p.get("text"),
            "thought": p.get("thought"),
            "mood": normalize_mood(p.get("mood")),
        }
    return None
