"""The winner celebration — a modular, opt-in-by-data champion overlay.

Mirrors the verdict tests' discipline: the gate (no winner → empty string) is exercised
against real offline snapshots, and the agent/team/ground-truth flavours against hand-built
``vm`` dicts that match the shipped ``view_model_at`` contract.
"""

from __future__ import annotations

from src.core.conductor import Conductor
from src.core.ledger_factory import make_ledger
from src.core.registry import default_registry
from src.tools.builtins import default_tool_registry
from src.ui.fishbowl import view_model_at
from src.ui.fishbowl.render.winner import render_winner


def _offline_vm(scenario: str = "thousand-token-wood") -> dict:
    reg = default_registry()
    c = Conductor(
        reg.build_scenario(scenario, tools=default_tool_registry()),
        governor=reg.governor_for(scenario),
        ledger=make_ledger(),
    )
    c.reset(c.scenario.default_seed)
    c.step(n_ticks=6)
    cast = [a.manifest for a in c.scenario.agents]
    return view_model_at(c.ledger.events, len(c.ledger.events), cast, scenario_name=scenario)


class TestWinnerGate:
    def test_empty_without_a_winner(self):
        # missing verdict, explicit None, and a verdict with no winner all render nothing.
        assert render_winner({}) == ""
        assert render_winner({"verdict": None}) == ""
        assert render_winner({"verdict": {"text": "Inconclusive.", "winner_label": None}}) == ""

    def test_none_kind_scenario_stays_silent(self):
        # a real no-winner run (the Wood) never crowns anyone → no celebration, ever.
        assert render_winner(_offline_vm()) == ""


class TestAgentWin:
    def _vm(self, **over) -> dict:
        vm = {
            "cast": [
                {"id": "hypothesis-former", "name": "hypothesis-former", "archetype": "the analyst",
                 "model": "Arch-Router-1.5B", "hue": 200},
                {"id": "devil-advocate", "name": "devil-advocate", "archetype": "the skeptic",
                 "model": "Qwen", "hue": 20},
            ],
            "teams": {},
            "verdict": {
                "winner": "hypothesis-former",
                "winner_label": "Hypothesis Former",
                "winner_kind": "agent",
                "correct": None,
            },
        }
        vm["verdict"].update(over)
        return vm

    def test_names_the_winning_bot_with_model_and_archetype(self):
        html = render_winner(self._vm())
        assert 'class="winner-fx"' in html
        assert "Hypothesis Former" in html  # the winner label, front and centre
        assert "the analyst" in html  # its archetype
        assert "Arch-Router-1.5B" in html  # the model that ran
        assert "Champion" in html and "&#127942;" in html  # trophy flavour

    def test_is_dismissable_via_backdrop_and_close_button(self):
        # The celebration can be closed without JS: a hidden checkbox toggled by the
        # backdrop (click-elsewhere) and the ✕ button, both <label for> the same control.
        html = render_winner(self._vm())
        assert 'class="wf-dismiss"' in html and 'id="wf-dismiss"' in html
        assert 'class="wf-backdrop" for="wf-dismiss"' in html  # click-elsewhere to close
        assert 'class="wf-x" for="wf-dismiss"' in html  # the styled ✕ button
        assert html.count('for="wf-dismiss"') == 2

    def test_tints_with_the_winning_bots_hue(self):
        html = render_winner(self._vm())
        assert "--win-hue:200" in html  # the analyst's hue drives the glow

    def test_unknown_winner_id_still_celebrates_the_label(self):
        # a winner not present in the cast (legacy/edge) still gets a card, just no chips.
        html = render_winner(self._vm(winner="ghost", winner_label="Ghost"))
        assert 'class="winner-fx"' in html
        assert "Ghost" in html
        assert "wf-model" not in html  # no model chip when the bot isn't in the cast


class TestTeamWin:
    def _vm(self, **over) -> dict:
        vm = {
            "cast": [
                {"id": "spy-cara", "name": "spy-cara", "archetype": "the herd", "model": "M", "hue": 95},
                {"id": "spy-bex", "name": "spy-bex", "archetype": "the herd", "model": "M", "hue": 95},
                {"id": "spy-nil", "name": "spy-nil", "archetype": "the spy", "model": "M", "hue": 20},
            ],
            "teams": {"herd": ["spy-cara", "spy-bex"], "spy": ["spy-nil"]},
            "verdict": {
                "winner": "herd",
                "winner_label": "Team Herd",
                "winner_kind": "team",
                "correct": True,
            },
        }
        vm["verdict"].update(over)
        return vm

    def test_lines_up_the_roster_and_names_the_losing_side(self):
        html = render_winner(self._vm())
        assert "Team Herd" in html
        assert "spy-cara" in html and "spy-bex" in html  # the winning roster as chips
        assert "spy-nil" not in html  # the losing member is not lined up as a champion
        assert "Team Spy" in html  # the dethroned side is named
        assert "Champions" in html

    def test_ground_truth_miss_is_a_clean_getaway(self):
        # the spy slipped past the herd: still a win for the spy, celebrated with a wink.
        html = render_winner(self._vm(winner="spy", winner_label="Team Spy", correct=False))
        assert "Clean Getaway" in html
        assert "&#129399;" in html  # raccoon, not trophy
        assert "&#127942;" not in html


class TestEscaping:
    def test_label_is_escaped(self):
        vm = {"verdict": {"winner": "x", "winner_label": "<script>x</script>", "winner_kind": "agent"}, "cast": [], "teams": {}}
        html = render_winner(vm)
        assert "<script>" not in html and "&lt;script&gt;" in html
