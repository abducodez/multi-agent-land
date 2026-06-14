"""Pure HTML renderer for the Fishbowl narrator/event feed (the transcript).

This mirrors the ``Feed`` component of the React prototype (``ui/raw/show.jsx``) and the
feed styling in ``ui/raw/show.css``.  It is a *pure* string function over the view-model
dict produced by :func:`src.ui.fishbowl.view_model_at` — no Gradio import, no sibling
render imports — so the same projection can be served as HTML by the Gradio shell or a
future ``gr.Server``.

Each feed item (``vm["feed"][i]``) is tagged by ``kind``:

* ``narrate{voice,text}``  → the narrator's voice + line, with an optional typewriter
  slice on the *head* (latest) narrate item.
* ``say{agent,said,thought,mood}`` → an agent name + what it said aloud, and — when the
  mind-reader is on and a thought exists — the inner thought in a ``thought`` span.
* ``poke{label,text}`` → a bolt-tagged disturbance line.
* ``verdict{text,reveal,agent}`` → a lime-accented verdict line.

Every piece of model/agent text is escaped with :func:`html.escape`.
"""

from __future__ import annotations

import html

from src.ui.fishbowl.adapter import agent_color, agent_color_dim

_BOLT = "⚡"  # poke lines carry a lightning bolt
_SCALES = "⚖"  # the verdict line carries the scales of judgement
_QUILL = "✒"  # the rafters-critic tags its color commentary


def _last_narrate_index(feed: list[dict]) -> int | None:
    """Index of the latest ``narrate`` item — the one that gets the typewriter slice."""
    for i in range(len(feed) - 1, -1, -1):
        if feed[i].get("kind") == "narrate":
            return i
    return None


def _narrate_line(*, voice_name: str, text: str, typing: bool) -> str:
    speaker = html.escape(voice_name)
    body = html.escape(text)
    p_cls = ' class="caret"' if typing else ""
    return f'<div class="fe narr"><span class="narr-voice">{speaker}</span><p{p_cls}>{body}</p></div>'


def _say_line(item: dict, *, mind_reader: bool) -> str:
    name = html.escape(item.get("agent") or "")
    said = html.escape(item.get("said") or "")
    line = f'<div class="say-line"><b class="disp">{name}</b><span>{said}</span></div>'
    thought = item.get("thought")
    if mind_reader and thought:
        thought_html = html.escape(thought)
        line += f'<div class="say-think">↳ <i class="thought">{thought_html}</i></div>'
    # The line wears the speaker's own phosphor (name, accent border, thought tint) so the
    # transcript is colour-coded to the cast — the same hue as their MindCard and avatar.
    # A hue-less line (e.g. an un-cast actor) falls back to the CSS default accent.
    hue = item.get("hue")
    style = f' style="--ac:{agent_color(int(hue))};--acd:{agent_color_dim(int(hue))}"' if hue is not None else ""
    return f'<div class="fe say"{style}>{line}</div>'


def _poke_line(item: dict) -> str:
    label = html.escape(item.get("label") or "DISTURBANCE")
    text = html.escape(item.get("text") or "")
    return f'<div class="fe poke"><span class="poke-tag">{_BOLT} {label}</span><p>{text}</p></div>'


def _verdict_line(item: dict) -> str:
    text = html.escape(item.get("text") or "")
    return f'<div class="fe verdict-fe"><span class="poke-tag">{_SCALES} VERDICT</span><p>{text}</p></div>'


def _commentate_line(item: dict) -> str:
    """A color-commentary card: a funny line plus an optional image and audio clip.

    Degrades gracefully — with neither image nor audio it is just the tagged line, so a
    text-only beat (offline before media is wired, or a live media failure) still renders.
    ``src`` is a ``/file=`` URL or a ``data:`` URI: it is attribute-escaped (``quote=True``),
    NEVER body-escaped; the caption and ``alt`` are body-escaped."""
    text = html.escape(item.get("text") or "")
    parts = [
        f'<span class="poke-tag cm-tag">{_QUILL} FROM THE RAFTERS</span>',
        f'<p class="cm-text">{text}</p>',
    ]
    image = item.get("image") or {}
    if image.get("src"):
        alt = html.escape(image.get("alt") or "the critic's vision")
        parts.append(f'<img class="cm-img" src="{html.escape(image["src"], quote=True)}" alt="{alt}" loading="lazy">')
    audio = item.get("audio") or {}
    if audio.get("src"):
        parts.append(
            f'<audio class="cm-audio" controls preload="none" src="{html.escape(audio["src"], quote=True)}"></audio>'
        )
    # Wear the critic's own phosphor (its manifest hue), like every other coloured feed line.
    hue = item.get("hue")
    style = f' style="--ac:{agent_color(int(hue))};--acd:{agent_color_dim(int(hue))}"' if hue is not None else ""
    return f'<div class="fe commentate"{style}>' + "".join(parts) + "</div>"


def render_feed(
    vm: dict,
    *,
    mind_reader: bool,
    typed_n: int | None = None,
    dense: bool = False,
) -> str:
    """Render the narrator/event transcript as a single HTML string.

    Parameters
    ----------
    vm:
        A view-model dict from :func:`src.ui.fishbowl.view_model_at`.  Reads
        ``vm["feed"]`` and ``vm["voice_meta"]["name"]``.
    mind_reader:
        When ``True``, ``say`` lines append the agent's inner ``thought``.
    typed_n:
        When not ``None``, the *head* (latest) ``narrate`` line is sliced to this many
        characters — the typewriter effect.  A trailing caret is shown while the slice is
        shorter than the full text.
    dense:
        When ``True``, add the compact ``dense`` modifier to the feed container.
    """
    feed = vm.get("feed") or []
    voice_name = (vm.get("voice_meta") or {}).get("name") or "NARRATOR"
    head_narrate = _last_narrate_index(feed)

    rows: list[str] = []
    for idx, item in enumerate(feed):
        kind = item.get("kind")
        if kind == "narrate":
            full = item.get("text") or ""
            if typed_n is not None and idx == head_narrate:
                shown = full[: max(0, typed_n)]
                typing = len(shown) < len(full)
            else:
                shown = full
                typing = False
            rows.append(_narrate_line(voice_name=voice_name, text=shown, typing=typing))
        elif kind == "poke":
            rows.append(_poke_line(item))
        elif kind == "verdict":
            rows.append(_verdict_line(item))
        elif kind == "say":
            rows.append(_say_line(item, mind_reader=mind_reader))
        elif kind == "commentate":
            rows.append(_commentate_line(item))
        # unknown kinds are silently skipped

    container_cls = "feed scroll dense" if dense else "feed scroll"
    return f'<div class="{container_cls}">' + "".join(rows) + "</div>"
