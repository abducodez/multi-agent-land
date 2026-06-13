"""The FISHBOWL Gradio shell builds standalone (Unit 9) — mock-free.

These tests exercise the *integrator* surface only: that the two-tab Blocks builds even
when every leaf module (theme/render/session/show/lab) is absent (defensive placeholders),
that the package import stays Gradio-free, that the root shim re-exports the app, and that
the HTML-composition + fallback-session transport work end-to-end offline (no API key).
"""

from __future__ import annotations

import gradio as gr


def test_build_app_builds_with_placeholders() -> None:
    from src.ui.fishbowl.app import build_app

    demo = build_app()
    assert isinstance(demo, gr.Blocks)


def test_module_level_demo_exists() -> None:
    from src.ui.fishbowl.app import demo

    assert isinstance(demo, gr.Blocks)


def test_package_import_is_gradio_free() -> None:
    """Importing the package must not pull in Gradio (pure presenter promise)."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", "import src.ui.fishbowl, sys; print('gradio' in sys.modules)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "False"


def test_lazy_exports_resolve() -> None:
    import src.ui.fishbowl as pkg

    assert callable(pkg.build_app)
    assert isinstance(pkg.demo, gr.Blocks)


def test_root_shim_imports() -> None:
    import app

    assert isinstance(app.demo, gr.Blocks)
    assert callable(app.launch)


def test_fallback_session_transport_offline() -> None:
    """The placeholder session steps a real Conductor and snapshots a prefix view."""
    from src.ui.fishbowl.app import SCENARIOS, _new_session

    name = next(iter(SCENARIOS.values()))
    session = _new_session(name)
    session.reset("")  # default seed; genesis events appended
    assert session.head > 0

    before = session.head
    session.step()
    assert session.head >= before  # stepping never rewinds the head

    vm = session.snapshot(0)  # pure prefix view at k=0
    assert vm["step"] == 0
    assert "cast" in vm and "feed" in vm

    vm_head = session.snapshot(session.head)
    assert vm_head["step"] == session.head


def test_render_show_html_returns_four_panes() -> None:
    from src.ui.fishbowl.app import SCENARIOS, _new_session, render_show_html

    session = _new_session(next(iter(SCENARIOS.values())))
    session.reset("")
    vm = session.snapshot(session.head)

    panes = render_show_html(vm, layout="constellation", mind_reader=True)
    assert isinstance(panes, tuple) and len(panes) == 4
    assert all(isinstance(p, str) for p in panes)

    # Each layout produces a string stage without raising.
    for layout in ("constellation", "split", "feed"):
        stage, _feed, _meters, _verdict = render_show_html(vm, layout=layout, mind_reader=False)
        assert isinstance(stage, str)


def test_render_show_html_overlays_thinking_strip_on_stage() -> None:
    # The "who's thinking…" hint rides on the always-visible stage pane (not the feed,
    # which resets its scroll on every re-render). It appears only when asked.
    from src.ui.fishbowl.app import SCENARIOS, _new_session, render_show_html

    session = _new_session(next(iter(SCENARIOS.values())))
    session.reset("")
    vm = session.snapshot(session.head)

    stage_plain, *_ = render_show_html(vm, layout="constellation")
    assert "thinking-strip" not in stage_plain

    stage_hint, *_ = render_show_html(vm, layout="constellation", thinking="scene-whisperer is thinking")
    assert "thinking-strip" in stage_hint
    assert "scene-whisperer is thinking" in stage_hint
    # Prepended (first child) so position:sticky pins it to the TOP of the stage — in
    # view without scrolling, rather than below the fold at the bottom.
    assert stage_hint.index("thinking-strip") < stage_hint.index('class="constellation"')


def test_thinking_strip_escapes_its_label() -> None:
    from src.ui.fishbowl.app import _thinking_strip

    html = _thinking_strip("<script>alert(1)</script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_inject_appends_a_poke() -> None:
    from src.ui.fishbowl.app import SCENARIOS, _new_session

    session = _new_session(next(iter(SCENARIOS.values())))
    session.reset("")
    before = session.head
    session.inject("A lantern starts whispering recipes.", label="DISTURBANCE")
    assert session.head > before
