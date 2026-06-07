"""Mock-free tests for the Fishbowl pure HTML renderers (Unit 2).

Covers the mood-driven avatar SVG and the say-vs-think MindCard flip card, including the
sealed-thought behaviour when the mind-reader is off and the verdict back face.
"""

from __future__ import annotations

import html

from src.ui.fishbowl.adapter import TIER_COLOR
from src.ui.fishbowl.render import render_avatar, render_mindcard


def _card(**overrides) -> dict:
    """A cast card matching the ``view_model_at(...)["cast"][i]`` contract."""
    card = {
        "id": "Birch",
        "name": "Birch",
        "archetype": "the wary elder",
        "hue": 200,
        "role": "saboteur",
        "model_profile": "balanced",
        "tier": "mid",
        "said": "I planted nothing in the grove.",
        "thought": "They must never find the seedling cache.",
        "mood": "lying",
        "mood_label": "bluffing",
        "spoke": True,
        "speaking": True,
    }
    card.update(overrides)
    return card


# ── avatar ──────────────────────────────────────────────────────────────────────


def test_avatar_is_svg_with_hue_colour():
    out = render_avatar(120, "smug")
    assert out.startswith("<div")
    assert "<svg" in out
    assert "oklch(0.82 0.14 120)" in out
    assert "av-smug" in out


def test_avatar_panic_has_sweat_and_gasp_and_ring_when_active():
    out = render_avatar(200, "panic", 64, True)
    assert "<svg" in out
    assert "av-panic" in out
    assert "av-sweat" in out  # panic sweats
    assert "av-gasp" in out  # open 'o' mouth
    assert "av-ring" in out  # active ring


def test_avatar_thinking_blinks_and_lying_sweats():
    assert "av-blink" in render_avatar(40, "thinking")
    assert "av-sweat" in render_avatar(40, "lying")
    assert "av-sweat" not in render_avatar(40, "calm")


def test_avatar_size_is_applied():
    out = render_avatar(10, "calm", size=128)
    assert 'width="128"' in out
    assert "width:128px" in out


# ── mindcard ──────────────────────────────────────────────────────────────────


def test_mindcard_front_shows_name_said_and_thought_when_reading():
    card = _card()
    out = render_mindcard(card, mind_reader=True)
    assert card["name"] in out
    assert "mind-" in out
    assert "mind mind-ring" in out  # default variant
    assert html.escape(card["said"]) in out
    assert html.escape(card["thought"]) in out
    assert card["mood_label"] in out
    assert "actually thinking" in out
    assert TIER_COLOR[card["tier"]] in out


def test_mindcard_seals_thought_when_mind_reader_off():
    card = _card()
    out = render_mindcard(card, mind_reader=False)
    assert html.escape(card["thought"]) not in out  # thought hidden
    assert "mind sealed" in out  # sealed placeholder tag
    assert "seal" in out
    assert html.escape(card["said"]) in out  # said is still public


def test_mindcard_speaking_and_panic_classes():
    out = render_mindcard(_card(speaking=True, mood="panic"), mind_reader=True)
    assert "speaking" in out
    assert "rattled" in out
    assert 'class="mic"' in out


def test_mindcard_variant_and_unspoken_placeholder():
    card = _card(said=None, thought=None, spoke=False, speaking=False)
    out = render_mindcard(card, mind_reader=True, variant="stage")
    assert "mind mind-stage" in out
    assert "— hasn't spoken —" in out
    assert "— quiet in here —" in out


def test_mindcard_back_face_reveal_when_flipped():
    card = _card()
    out = render_mindcard(card, mind_reader=True, flipped=True, secret="The saboteur — saboteur")
    assert "flipped" in out
    assert "mind-back" in out
    assert "the truth" in out
    assert "The saboteur — saboteur" in out


def test_mindcard_back_face_absent_when_not_flipped():
    out = render_mindcard(_card(), mind_reader=True, flipped=False)
    assert "mind-back" not in out


def test_mindcard_escapes_html():
    card = _card(name="<b>X</b>", said="a & b <script>", thought="c < d")
    out = render_mindcard(card, mind_reader=True)
    assert "<b>X</b>" not in out
    assert "&lt;b&gt;X&lt;/b&gt;" in out
    assert "<script>" not in out
