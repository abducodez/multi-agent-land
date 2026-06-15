"""The About tab — a compact, self-contained tour of Multi-Agent Land.

What it is, how it works (an HTML/CSS recreation of the agent-ensemble architecture so
it scales crisply and lives in the CRT theme rather than a flat PNG), and the links a
judge or visitor needs: the submission video, the GitHub repo, LinkedIn, and the three
Hugging Face field-notes articles.

Pure HTML — no state, no callbacks. Everything is scoped under ``.fishbowl`` so the
theater stylesheet applies; the About-only rules live in the inline ``<style>`` block.
"""

from __future__ import annotations

import gradio as gr

# ── links ────────────────────────────────────────────────────────────────────────
# Repo + articles are known; the video and LinkedIn URLs are filled in by the team
# (left as obvious placeholders so a missing link reads as TODO, never as broken chrome).
GITHUB_URL = "https://github.com/abducodez/multi-agent-land"
VIDEO_URL = "https://youtu.be/v8-zR6eTbDM"
LINKEDIN_URL = "https://www.linkedin.com/in/gharsallah/"
ARTICLES = [
    ("The overview", "https://huggingface.co/blog/build-small-hackathon/multi-agents-land"),
    (
        "Different scenarios",
        "https://huggingface.co/blog/build-small-hackathon/six-playable-woods-and-a-fishbowl-to-watch-them",
    ),
    ("Architecture", "https://huggingface.co/blog/build-small-hackathon/one-engine-three-costumes"),
]


# ── architecture diagram (HTML/CSS recreation of agent_ensemble_architecture.png) ──


def _node(title: str, sub: str, *, tone: str, dashed: bool = False) -> str:
    """One labelled box in the diagram. ``tone`` ∈ {engine, ledger, oversight}."""
    cls = f"ab-node ab-{tone}" + (" ab-dashed" if dashed else "")
    return f'<div class="{cls}"><div class="ab-node-title">{title}</div><div class="ab-node-sub">{sub}</div></div>'


def _architecture_html() -> str:
    """Three columns — engine · ledger · oversight & UI — wired by arrow glyphs.

    Mirrors the source diagram: the Conductor → Context builder → Worker agents → Tools
    pipeline on the left, the append-only Event ledger in the centre, and the Observer /
    Judge oversight feeding the Client UI on the right."""
    engine = (
        _node("Conductor", "scheduler · budgets", tone="engine")
        + '<div class="ab-arrow ab-down">↓</div>'
        + _node("Context builder", "per-agent memory", tone="engine")
        + '<div class="ab-arrow ab-down">↓</div>'
        + _node("Worker agents", "small specialist models", tone="engine")
        + '<div class="ab-arrow ab-down">↕</div>'
        + _node("Tools (MCP)", "pluggable via registry", tone="engine")
        + _node("+ register new module", "", tone="engine", dashed=True)
    )
    ledger = _node("Event ledger", "append-only event log", tone="ledger")
    oversight = (
        _node("Client UI", "renders the stream", tone="oversight")
        + '<div class="ab-arrow ab-up">↑</div>'
        + _node("Observer agent", "renders the stream", tone="oversight")
        + _node("Judge / moderator", "promote · gate · decide", tone="oversight")
    )
    legend = (
        '<div class="ab-legend">'
        '<span class="ab-key ab-engine"></span>engine'
        '<span class="ab-key ab-ledger"></span>ledger'
        '<span class="ab-key ab-oversight"></span>oversight &amp; UI'
        "</div>"
    )
    return (
        '<div class="ab-arch">'
        f'<div class="ab-col ab-col-engine">{engine}</div>'
        '<div class="ab-wire ab-wire-l">→</div>'
        f'<div class="ab-col ab-col-ledger">{ledger}</div>'
        '<div class="ab-wire ab-wire-r">↔</div>'
        f'<div class="ab-col ab-col-oversight">{oversight}</div>'
        "</div>" + legend
    )


# ── tab body ───────────────────────────────────────────────────────────────────────

_ABOUT_CSS = """
.fishbowl .ab-wrap { max-width: 1080px; margin: 0 auto; padding: 28px 22px 40px; line-height: 1.65; }
.fishbowl .ab-hero h1 { font-size: 30px; letter-spacing: 0.04em; color: var(--ink); }
.fishbowl .ab-hero .ab-tag { color: var(--cyan); font-style: italic; margin-top: 6px; }
.fishbowl .ab-lede { color: var(--ink-mid); margin: 16px 0 8px; font-size: 15px; }
.fishbowl .ab-lede b { color: var(--lime); font-weight: 600; }
.fishbowl .ab-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 12px; margin: 18px 0 6px; }
.fishbowl .ab-card { padding: 14px 16px; }
.fishbowl .ab-card h4 { color: var(--cyan); font-size: 13px; letter-spacing: 0.06em; margin-bottom: 6px; }
.fishbowl .ab-card p { color: var(--ink-mid); font-size: 13px; margin: 0; }

.fishbowl .ab-section-title { margin: 30px 0 14px; }

/* architecture diagram */
.fishbowl .ab-arch { display: grid; grid-template-columns: 1fr auto 1fr auto 1fr;
  align-items: start; gap: 8px; margin: 6px 0 10px; }
.fishbowl .ab-col { display: flex; flex-direction: column; gap: 6px; }
.fishbowl .ab-col-ledger { justify-content: center; align-self: stretch; }
.fishbowl .ab-node { border: 1.5px solid var(--line); border-radius: var(--r-lg);
  padding: 12px 14px; text-align: center; background: var(--panel); }
.fishbowl .ab-node-title { font-family: var(--font-display); font-weight: 700; font-size: 14px; }
.fishbowl .ab-node-sub { font-size: 11px; color: var(--ink-dim); margin-top: 3px; }
.fishbowl .ab-engine { border-color: var(--teal); background: rgba(43,196,180,0.10); }
.fishbowl .ab-engine .ab-node-title { color: var(--lime); }
.fishbowl .ab-ledger { border-color: var(--violet); background: rgba(155,140,255,0.12);
  min-height: 100%; display: flex; flex-direction: column; justify-content: center; }
.fishbowl .ab-ledger .ab-node-title { color: var(--violet); font-size: 16px; }
.fishbowl .ab-oversight { border-color: var(--coral); background: rgba(255,143,125,0.10); }
.fishbowl .ab-oversight .ab-node-title { color: var(--coral); }
.fishbowl .ab-dashed { border-style: dashed; background: transparent; opacity: 0.7; }
.fishbowl .ab-dashed .ab-node-title { font-size: 12px; color: var(--ink-dim); }
.fishbowl .ab-arrow { text-align: center; color: var(--ink-faint); font-size: 16px; line-height: 1; }
.fishbowl .ab-wire { align-self: center; color: var(--ink-faint); font-size: 22px; padding-top: 40px; }
.fishbowl .ab-legend { display: flex; align-items: center; justify-content: center; gap: 8px;
  flex-wrap: wrap; margin-top: 14px; color: var(--ink-dim); font-size: 12px; }
.fishbowl .ab-key { width: 13px; height: 13px; border-radius: 3px; display: inline-block;
  margin-left: 14px; border: 1.5px solid; }
.fishbowl .ab-key.ab-engine { border-color: var(--teal); background: rgba(43,196,180,0.18); }
.fishbowl .ab-key.ab-ledger { border-color: var(--violet); background: rgba(155,140,255,0.2); }
.fishbowl .ab-key.ab-oversight { border-color: var(--coral); background: rgba(255,143,125,0.18); }

/* link row */
.fishbowl .ab-links { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0 4px; }
.fishbowl a.ab-link { text-decoration: none; }
.fishbowl .ab-articles { margin: 10px 0 0; padding: 0; list-style: none; }
.fishbowl .ab-articles li { margin: 6px 0; }
.fishbowl .ab-articles a { color: var(--cyan); text-decoration: none; }
.fishbowl .ab-articles a:hover { color: #fff; text-shadow: var(--glow); }

@media (max-width: 760px) {
  .fishbowl .ab-arch { grid-template-columns: 1fr; }
  .fishbowl .ab-wire { display: none; }
}
"""


def _links_html() -> str:
    articles = "".join(
        f'<li>🔗 <a href="{url}" target="_blank" rel="noopener">{label}</a></li>' for label, url in ARTICLES
    )
    return f"""
<div class="fishbowl">
  <div class="ab-links">
    <a class="ab-link" href="{VIDEO_URL}" target="_blank" rel="noopener"><span class="btn primary">▶ Submission video</span></a>
    <a class="ab-link" href="{GITHUB_URL}" target="_blank" rel="noopener"><span class="btn"> GitHub</span></a>
    <a class="ab-link" href="{LINKEDIN_URL}" target="_blank" rel="noopener"><span class="btn">in LinkedIn</span></a>
  </div>
  <div class="ab-section-title eyebrow">Field notes &middot; articles</div>
  <ul class="ab-articles">{articles}</ul>
</div>
"""


def build_about() -> dict:
    """Render the About tab. Pure HTML islands wrapped in the ``.fishbowl`` scope root."""
    gr.HTML(f"<style>{_ABOUT_CSS}</style>")
    gr.HTML(
        """
<div class="fishbowl"><div class="ab-wrap">
  <div class="ab-hero">
    <h1>&#9673; Multi-Agent Land</h1>
    <div class="ab-tag">small minds &middot; one ledger &middot; &le; 32B</div>
  </div>
  <p class="ab-lede">
    A small-model, multi-agent interactive story engine built on an <b>append-only event
    ledger</b>. Agents never call each other &mdash; they post typed events to a shared log,
    and every view (stage, memory, UI) is a <b>projection</b> of that log. One tiny
    event-sourced engine powers many delightful worlds: a whimsical forest theater, a
    mystery-solving blackboard swarm, a tool-using oracle grove &mdash; not three apps, but
    three YAML configs of the <b>same engine</b>.
  </p>
  <div class="ab-grid">
    <div class="panel ab-card"><h4>Genuinely alive</h4><p>Watch small specialist agents
      write, judge, remember, and render strange interactive scenes in real time.</p></div>
    <div class="panel ab-card"><h4>AI is load-bearing</h4><p>The multi-agent drama is the
      product &mdash; the magic comes from the models, not the scaffolding.</p></div>
    <div class="panel ab-card"><h4>One engine, three costumes</h4><p>Agents bind to models by
      declarative YAML profile; each runs on the small model that fits its job.</p></div>
  </div>
</div></div>
"""
    )
    gr.HTML(
        f'<div class="fishbowl"><div class="ab-wrap" style="padding-top:0">'
        f'<div class="ab-section-title eyebrow">How it works &middot; the agent ensemble</div>'
        f"{_architecture_html()}</div></div>"
    )
    gr.HTML(f'<div class="ab-wrap" style="padding-top:0">{_links_html()}</div>')
    return {}
