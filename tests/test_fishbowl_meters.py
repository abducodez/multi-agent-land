"""Fishbowl meters + verdict renderers — pure HTML strings over a real view-model.

Mock-free per the repo convention: most cases build an actual ``vm`` via ``view_model_at``
over a real offline conductor run (so ``tokens_real`` carries the governor's real count);
the warning/budget-out and None-ceiling edges are exercised with hand-built snapshots that
match the shipped contract.
"""

from __future__ import annotations

from src.core.conductor import Conductor
from src.core.ledger_factory import make_ledger
from src.core.registry import default_registry
from src.tools.builtins import default_tool_registry
from src.ui.fishbowl import view_model_at
from src.ui.fishbowl.render.meters import render_meters, render_verdict


def _offline_vm(**kwargs) -> dict:
    reg = default_registry()
    c = Conductor(
        reg.build_scenario("thousand-token-wood", tools=default_tool_registry()),
        governor=reg.governor_for("thousand-token-wood"),
        ledger=make_ledger(),
    )
    c.reset(c.scenario.default_seed)
    c.step(n_ticks=6)
    cast = [a.manifest for a in c.scenario.agents]
    return view_model_at(
        c.ledger.events,
        len(c.ledger.events),
        cast,
        scenario_name="thousand-token-wood",
        governor=c.governor,
        **kwargs,
    )


class TestRenderMeters:
    def test_real_run_shows_meters_tokens_and_running_status(self):
        vm = _offline_vm(max_rounds=4)
        # a generous ceiling well above the run's real usage keeps the show RUNNING.
        vm["token_ceiling"] = vm["tokens_real"]["total_tokens"] * 4 + 1000
        html = render_meters(vm)
        assert 'class="meters panel"' in html
        # uses the governor's real total_tokens, not the scrubber estimate
        used = vm["tokens_real"]["total_tokens"]
        assert f"{used:,}" in html
        assert "RUNNING" in html and "BUDGET OUT" not in html
        assert "Round" in html and "Status" in html

    def test_no_ceiling_renders_no_bar_and_bare_token_count(self):
        vm = _offline_vm()  # token_ceiling defaults to None
        assert vm["token_ceiling"] is None
        html = render_meters(vm)
        assert 'class="bar"' not in html  # no progress bar without a ceiling
        assert "RUNNING" in html  # still running — no budget to blow
        # the bare token count carries no "used / ceiling" slash.
        used = vm["tokens_real"]["total_tokens"]
        assert f"{used:,} /" not in html

    def test_warning_colour_at_or_above_85_percent(self):
        vm = {"tokens": 0, "tokens_real": {"total_tokens": 880}, "token_ceiling": 1000, "rounds": 2, "max_rounds": 4}
        html = render_meters(vm)
        assert "var(--coral)" in html  # warning accent kicks in at >=85%
        assert "width:88%" in html
        assert "BUDGET OUT" not in html  # not out yet, just warning

    def test_budget_out_status_when_ceiling_reached(self):
        vm = {"tokens": 0, "tokens_real": {"total_tokens": 1200}, "token_ceiling": 1000, "rounds": 4, "max_rounds": 4}
        html = render_meters(vm)
        assert "BUDGET OUT" in html
        assert "width:100%" in html  # clamped, never overflows
        assert "1,200 / 1,000" in html

    def test_prefers_tokens_real_over_estimate(self):
        vm = {
            "tokens": 42,
            "tokens_real": {"total_tokens": 777},
            "token_ceiling": None,
            "rounds": 1,
            "max_rounds": None,
        }
        html = render_meters(vm)
        assert "777" in html and "42" not in html

    def test_falls_back_to_estimate_when_no_real_stats(self):
        vm = {"tokens": 321, "tokens_real": None, "token_ceiling": None, "rounds": 1, "max_rounds": None}
        html = render_meters(vm)
        assert "321" in html


class TestRenderVerdict:
    def test_empty_when_no_verdict(self):
        # an explicit None verdict and a missing key both render nothing.
        assert render_verdict({"verdict": None}) == ""
        assert render_verdict({}) == ""
        # a real offline snapshot renders a string (empty until the Judge rules).
        assert isinstance(render_verdict(_offline_vm()), str)

    def test_banner_with_reveal_lines(self):
        vm = {
            "verdict": {
                "text": "The lantern keeper kept the moon.",
                "reveal": [
                    {"agent": "pocket-actor", "secret": "I hid the key", "role": "thief"},
                    {"agent": "echo", "secret": "I watched", "role": "witness"},
                ],
                "agent": "mischief-critic",
            }
        }
        html = render_verdict(vm)
        assert 'class="verdict banner"' in html
        assert "The lantern keeper kept the moon." in html
        assert "pocket-actor" in html and "I hid the key" in html and "thief" in html
        assert "echo" in html and "witness" in html

    def test_html_is_escaped(self):
        vm = {"verdict": {"text": "<script>x</script>", "reveal": [{"agent": "a", "secret": "<b>", "role": "r"}]}}
        html = render_verdict(vm)
        assert "<script>" not in html and "&lt;script&gt;" in html
        assert "&lt;b&gt;" in html

    def test_verdict_with_empty_reveal_still_shows_text(self):
        html = render_verdict({"verdict": {"text": "Inconclusive.", "reveal": []}})
        assert 'class="verdict banner"' in html
        assert "Inconclusive." in html
