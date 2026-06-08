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


# ── moods + voices ──────────────────────────────────────────────────────────────


def normalize_mood(mood: str | None) -> str:
    return mood if mood in MOOD_META else "calm"


def mood_label(mood: str | None) -> str:
    return MOOD_META.get(normalize_mood(mood))[0]


def mood_color(mood: str | None) -> str:
    return MOOD_META.get(normalize_mood(mood))[1]


def scenario_voice(scenario_name: str) -> str:
    return _SCENARIO_VOICE.get(scenario_name, "doc")


# ── feed vocabulary (say / narrate / poke / verdict) ────────────────────────────


def event_to_feed_item(event: Event, cast_names: list[str] | None = None) -> dict | None:
    """Map one engine event to a Fishbowl feed item, or ``None`` to omit it."""
    kind = event.kind
    p = event.payload
    if kind == "world.observed":
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
